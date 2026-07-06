"""Bridge to the reused Auto-Research Stage-1 pipeline.

We reuse `ArxivFetcher` directly (clean: builds a query, hits the arXiv API,
dedups against per-project storage, returns dicts). Trend analysis is
reimplemented here in a side-effect-free way (TF-IDF top terms + category
distribution + wordcloud PNG) to avoid Auto-Research's cwd/prompt-file
coupling, while producing equivalent artifacts.

Adding a new paper source: implement a `fetch_*` function returning the same
paper-dict shape and call it from the discovery service.
"""
from __future__ import annotations

import logging
import os
import re
import socket
import sys
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.core.config import AUTO_RESEARCH_ROOT

logger = logging.getLogger("far.arxiv")

# arXiv id shapes: new "2401.12345"(v2) and old "cond-mat/9901001". Hosts/path for URLs.
_ARXIV_NEW = re.compile(r"^\d{4}\.\d{4,5}(v\d+)?$")
_ARXIV_OLD = re.compile(r"^[a-z-]+(?:\.[A-Z]{2})?/\d{7}(v\d+)?$")
_ARXIV_HOSTS = {"arxiv.org", "www.arxiv.org", "export.arxiv.org"}
_ARXIV_PATH = re.compile(r"^/(?:abs|pdf)/(?P<id>[^?#/]+?)(?:\.pdf)?/?$")

# Make `import src...` from the Auto-Research repo resolvable.
_AR_ROOT = str(AUTO_RESEARCH_ROOT)
if _AR_ROOT not in sys.path:
    sys.path.insert(0, _AR_ROOT)


# Paper-dict shape produced by ArxivFetcher._extract_paper_info:
#   id, title, authors[list], abstract, categories[list], primary_category,
#   published, updated, pdf_url, entry_url, comment, journal_ref, doi, fetched_at
PaperDict = dict[str, Any]


def build_pipeline_config(
    *, categories: list[str], keywords: list[str], max_results: int, papers_dir: Path
) -> dict[str, Any]:
    """Construct the config dict ArxivFetcher expects, with per-project storage."""
    papers_dir.mkdir(parents=True, exist_ok=True)
    return {
        "arxiv": {
            "categories": categories or ["cs.AI"],
            "keywords": keywords or [],
            "max_results": max_results,
            "sort_by": "submittedDate",
            "sort_order": "descending",
        },
        "storage": {"json_path": str(papers_dir)},
        "pipeline": {"analysis_backend": "llm", "summary_backend": "llm"},
    }


def fetch_arxiv_papers(config: dict[str, Any], days_back: int = 3) -> list[PaperDict]:
    """Fetch recent arXiv papers via the reused ArxivFetcher.

    `fetch_papers` saves only newly-seen papers and returns [] on a same-day
    re-run (dedup). We then load the full accumulated daily set so re-running
    discovery shows the project's papers rather than an empty result.
    """
    from src.crawler.arxiv_fetcher import ArxivFetcher  # type: ignore

    fetcher = ArxivFetcher(config)
    new_papers = fetcher.fetch_papers(days_back=days_back)
    daily = fetcher.get_daily_papers()
    return daily or new_papers


def parse_arxiv_id(raw: str) -> str | None:
    """Extract a clean arXiv id (version suffix kept) from a URL or bare id.

    Accepts: "2401.12345", "2401.12345v2", "cond-mat/9901001",
    "https://arxiv.org/abs/2401.12345", "arxiv.org/pdf/2401.12345v1.pdf",
    "https://export.arxiv.org/abs/2401.12345". Returns None if unrecognizable.
    """
    s = (raw or "").strip()
    # Strip a leading "arXiv:" token — the exact form the UI displays paper ids in,
    # and a common copy-paste shape (arXiv pages, BibTeX, citations).
    s = re.sub(r"^arxiv:\s*", "", s, flags=re.IGNORECASE)
    if not s:
        return None
    # URL form → pull the id out of the /abs/ or /pdf/ path.
    if "/" in s and ("." in s.split("/")[0] or s.lower().startswith("http")):
        parsed = urlparse(s if "//" in s else f"//{s}")
        if (parsed.netloc or "").lower() in _ARXIV_HOSTS:
            m = _ARXIV_PATH.match(parsed.path)
            if m:
                cand = m.group("id")
                if _ARXIV_NEW.match(cand) or _ARXIV_OLD.match(cand):
                    return cand
    # Bare id (new or old scheme), version suffix allowed.
    if _ARXIV_NEW.match(s) or _ARXIV_OLD.match(s):
        return s
    return None


def fetch_arxiv_paper(arxiv_id: str) -> PaperDict | None:
    """Fetch ONE paper's metadata by arXiv id via the `arxiv` library's id_list lookup.

    Returns the same paper-dict shape as the bulk fetcher (id/title/authors/abstract/
    categories/published/pdf_url/...) so the rest of the pipeline treats it identically;
    None if the id is not found. Bounded by a process socket timeout (the arxiv library
    sets none of its own) so a slow CDN response fails fast instead of hanging.
    """
    import arxiv  # type: ignore  # only resolvable inside backend/.venv

    from src.utils import normalize_paper_pdf_url  # type: ignore

    timeout = float(os.environ.get("ARXIV_HTTP_TIMEOUT", "12"))
    previous = socket.getdefaulttimeout()
    socket.setdefaulttimeout(timeout)
    try:
        client = arxiv.Client(num_retries=int(os.environ.get("ARXIV_NUM_RETRIES", "2")))
        result = next(client.results(arxiv.Search(id_list=[arxiv_id], max_results=1)), None)
    except Exception as exc:  # noqa: BLE001 — surface as "not found" to the caller
        logger.warning("arXiv id lookup failed for %s: %s", arxiv_id, exc)
        return None
    finally:
        socket.setdefaulttimeout(previous)
    if result is None:
        return None
    return normalize_paper_pdf_url(
        {
            "id": result.entry_id.split("/")[-1],
            "title": (result.title or "").strip(),
            "authors": [a.name for a in result.authors],
            "abstract": (result.summary or "").replace("\n", " ").strip(),
            "categories": list(result.categories or []),
            "primary_category": result.primary_category,
            "published": result.published.isoformat() if result.published else "",
            "updated": result.updated.isoformat() if result.updated else "",
            "pdf_url": result.pdf_url or "",
            "entry_url": result.entry_id,
            "doi": getattr(result, "doi", None),
            "source": "arxiv",
        }
    )


def analyze_trends(papers: list[PaperDict], wordcloud_path: Path) -> dict[str, Any]:
    """Deterministic trend analysis: top TF-IDF terms, category counts, wordcloud.

    Returns a JSON-serializable dict; writes a wordcloud PNG to `wordcloud_path`.
    """
    if not papers:
        return {"paper_count": 0, "top_keywords": [], "categories": {}, "wordcloud_path": ""}

    docs = [f"{p.get('title', '')} . {p.get('abstract', '')}" for p in papers]

    top_keywords = _tfidf_top_terms(docs, top_n=30)
    categories = Counter()
    for p in papers:
        for c in p.get("categories", []) or []:
            categories[c] += 1

    wc_written = _render_wordcloud(top_keywords, wordcloud_path)

    return {
        "paper_count": len(papers),
        "top_keywords": [{"term": t, "weight": round(w, 4)} for t, w in top_keywords],
        "categories": dict(categories.most_common()),
        "wordcloud_path": str(wordcloud_path) if wc_written else "",
    }


def _tfidf_top_terms(docs: list[str], top_n: int = 30) -> list[tuple[str, float]]:
    from sklearn.feature_extraction.text import TfidfVectorizer

    try:
        vec = TfidfVectorizer(
            stop_words="english",
            ngram_range=(1, 2),
            max_features=2000,
            token_pattern=r"(?u)\b[a-zA-Z][a-zA-Z\-]{2,}\b",
        )
        matrix = vec.fit_transform(docs)
    except ValueError:
        return []
    scores = matrix.sum(axis=0).A1
    terms = vec.get_feature_names_out()
    ranked = sorted(zip(terms, scores), key=lambda kv: kv[1], reverse=True)
    return [(t, float(s)) for t, s in ranked[:top_n]]


def _render_wordcloud(top_keywords: list[tuple[str, float]], out_path: Path) -> bool:
    if not top_keywords:
        return False
    try:
        import matplotlib

        matplotlib.use("Agg")
        from wordcloud import WordCloud

        freqs = {t: w for t, w in top_keywords}
        wc = WordCloud(width=1000, height=500, background_color="white", colormap="viridis")
        wc.generate_from_frequencies(freqs)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        wc.to_file(str(out_path))
        return True
    except Exception:  # noqa: BLE001 — wordcloud is best-effort, never fatal
        return False
