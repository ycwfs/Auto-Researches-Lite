"""Convert a paper's PDF to markdown/text for use as idea-generation context.

Resolution order:
  1. MinerU API when MINERU_API_KEY (+ MINERU_API_URL) are configured,
  2. offline fallback: download the PDF and extract text with pypdf,
  3. last resort: the paper abstract.
Results are cached per paper under the project data dir.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from app.integrations.sources.http import request_with_retry

logger = logging.getLogger("far.mineru")

# Store essentially the whole paper; the summarizer trims to its own token budget.
# A high safety bound only guards against pathological (e.g. 100s-of-pages) PDFs.
_MAX_CHARS = 200000

# A browser-ish User-Agent — some hosts 403 the default python-requests UA. (It does
# NOT defeat IP-based bot blocking, e.g. OpenReview's Cloudflare — that's what the
# arXiv-by-title fallback below is for.)
_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


def _norm_title(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def find_arxiv_pdf_by_title(title: str) -> str:
    """Resolve a paper to its arXiv PDF URL by title, or "" when there's no confident
    match. Fallback for PDFs that can't be fetched server-side (e.g. OpenReview behind
    Cloudflare): most top-venue papers are also on arXiv, which IS fetchable. A strict
    title match (normalized equality or ≥0.9 token overlap) guards against wrong hits."""
    import urllib.parse
    import xml.etree.ElementTree as ET

    q = _norm_title(title)
    if len(q) < 12:  # too short/ambiguous to match safely
        return ""
    api = "http://export.arxiv.org/api/query?" + urllib.parse.urlencode(
        {"search_query": f'ti:"{title}"', "max_results": "5"}
    )
    try:
        r = request_with_retry("GET", api, headers={"User-Agent": _UA}, timeout=15)
        if r.status_code != 200:
            return ""
        root = ET.fromstring(r.text)
    except Exception as exc:  # noqa: BLE001
        logger.info("arXiv title lookup failed for %r: %s", title[:60], exc)
        return ""
    ns = {"a": "http://www.w3.org/2005/Atom"}
    want = set(q.split())
    for entry in root.findall("a:entry", ns):
        cand = _norm_title(entry.findtext("a:title", default="", namespaces=ns))
        cw = set(cand.split())
        overlap = len(want & cw) / max(1, len(want | cw))
        if cand == q or overlap >= 0.9:
            eid = entry.findtext("a:id", default="", namespaces=ns) or ""
            m = re.search(r"arxiv\.org/abs/(.+?)(v\d+)?$", eid.strip())
            if m:
                logger.info("arXiv fallback matched %r → %s", title[:60], m.group(1))
                return f"https://arxiv.org/pdf/{m.group(1)}"
    return ""


@dataclass
class ExtractResult:
    """Outcome of a PDF→text extraction, carrying proof of which path ran."""

    text: str
    method: str  # "mineru" | "pypdf" | "abstract" | "cache"
    chars: int
    cache_file: str
    # The URL the text was actually parsed from — set to the arXiv URL when the
    # arXiv-by-title fallback was used (so callers can persist an accessible link).
    source_url: str = ""


def extract(
    paper: dict[str, Any] | Any, cache_dir: Path, *,
    api_key: str = "", api_url: str = "", max_wait: int = 0, force: bool = False,
) -> ExtractResult:
    """Extract a paper's full text, reporting the method used (for provenance).

    Tries the MinerU API (admin-configured `api_key`/`api_url`, falling back to the
    MINERU_API_* env vars), then a pypdf download, then the abstract as a last
    resort. Caches the result; a cache hit reports method="cache". `max_wait` bounds
    the MinerU async poll (0 = built-in default). `force` ignores + overwrites the
    file cache — used to RETRY a paper that previously fell back to the abstract.
    """
    pdf_url = _get(paper, "pdf_url")
    abstract = _get(paper, "abstract")
    title = _get(paper, "title")
    ident = _get(paper, "arxiv_id") or _get(paper, "id") or title
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{hashlib.sha1((ident or title).encode()).hexdigest()[:16]}.md"
    if cache_file.exists() and not force:
        text = cache_file.read_text(encoding="utf-8")[:_MAX_CHARS]
        return ExtractResult(text, "cache", len(text), str(cache_file))

    def _fetch(u: str) -> tuple[str, str]:
        """(text, method) for one PDF URL — MinerU first, then a pypdf download."""
        t = _try_mineru(u, api_key, api_url, max_wait=max_wait)
        if t:
            return t, "mineru"
        t = _try_pypdf(u)
        return (t, "pypdf") if t else ("", "")

    text, method, source_url = "", "abstract", ""
    if pdf_url:
        text, method = _fetch(pdf_url)
        if text:
            source_url = pdf_url
    # arXiv-by-title fallback: the given URL couldn't be fetched (e.g. OpenReview
    # behind Cloudflare blocks datacenter IPs — MinerU's servers too). Most venue
    # papers are also on arXiv, which is fetchable; retry there.
    if not text and title and "arxiv.org" not in (pdf_url or ""):
        alt = find_arxiv_pdf_by_title(title)
        if alt:
            text, method = _fetch(alt)
            if text:
                source_url = alt  # the accessible link that actually worked
    if not text:
        text, method = f"# {title}\n\n{abstract}", "abstract"  # last resort

    text = text[:_MAX_CHARS]
    cache_file.write_text(text, encoding="utf-8")
    return ExtractResult(text, method, len(text), str(cache_file), source_url)


def to_markdown(paper: dict[str, Any] | Any, cache_dir: Path) -> str:
    """Return markdown/text for a paper (Paper model or dict), cached."""
    return extract(paper, cache_dir).text


def _get(obj: Any, attr: str) -> str:
    if isinstance(obj, dict):
        return str(obj.get(attr, "") or "")
    return str(getattr(obj, attr, "") or "")


def _try_mineru(pdf_url: str, api_key: str = "", api_url: str = "", *, max_wait: int = 0) -> str:
    """Extract markdown via the MinerU API.

    Supports the hosted v4 async flow (POST a task → poll the task → download the
    result zip and read its markdown) and a synchronous endpoint that returns
    markdown inline. Returns "" on any failure so the caller falls back to pypdf.
    """
    api_key = api_key or os.environ.get("MINERU_API_KEY")
    api_url = api_url or os.environ.get("MINERU_API_URL")
    if not api_key or not api_url:
        return ""
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        resp = request_with_retry(
            "POST", api_url, headers=headers,
            json={"url": pdf_url, "enable_formula": True, "enable_table": True},
            timeout=60,
        )
        if resp.status_code != 200:
            logger.warning("MinerU submit returned %s", resp.status_code)
            return ""
        data = resp.json() or {}
        payload = data.get("data") or {}
        # Synchronous endpoint: markdown returned inline.
        direct = payload.get("markdown") or data.get("markdown") or data.get("text")
        if direct:
            return direct
        # v4 async: poll the task, then download the result zip.
        task_id = payload.get("task_id")
        if not task_id:
            return ""
        zip_url = _poll_mineru_task(f"{api_url.rstrip('/')}/{task_id}", headers, max_wait=max_wait)
        return _markdown_from_zip(zip_url) if zip_url else ""
    except Exception as exc:  # noqa: BLE001
        logger.warning("MinerU call failed: %s", exc)
        return ""


def _poll_mineru_task(
    task_url: str, headers: dict, *, attempts: int = 30, delay: float = 4.0, max_wait: int = 0
) -> str:
    """Poll a MinerU v4 task until done; return its full_zip_url (or "").
    `max_wait` (seconds, admin-set) overrides the default budget of attempts×delay
    (120 s); a larger value lets slow/large PDFs (e.g. OpenReview) finish."""
    import math
    import time

    if max_wait and max_wait > 0:
        attempts = max(1, math.ceil(max_wait / delay))
    for _ in range(attempts):
        r = request_with_retry("GET", task_url, headers=headers, timeout=30)
        if r.status_code != 200:
            return ""
        d = (r.json() or {}).get("data") or {}
        state = d.get("state")
        if state == "done":
            return d.get("full_zip_url") or ""
        if state == "failed":
            logger.warning("MinerU task failed: %s", d.get("err_msg"))
            return ""
        time.sleep(delay)
    logger.warning("MinerU task did not finish within the poll budget")
    return ""


def _markdown_from_zip(zip_url: str) -> str:
    """Download a MinerU result zip and return its markdown (prefers full.md)."""
    import io
    import zipfile

    r = request_with_retry("GET", zip_url, timeout=60)
    if r.status_code != 200 or not r.content:
        return ""
    try:
        zf = zipfile.ZipFile(io.BytesIO(r.content))
        names = zf.namelist()
        md = next((n for n in names if n.endswith("full.md")), None) or next(
            (n for n in names if n.endswith(".md")), None
        )
        return zf.read(md).decode("utf-8", errors="replace") if md else ""
    except Exception as exc:  # noqa: BLE001
        logger.warning("MinerU zip parse failed: %s", exc)
        return ""


def extract_from_bytes(data: bytes) -> str:
    """Extract text from raw PDF bytes (a user upload) via pypdf — no URL/network.

    Mirrors the offline pypdf branch of `extract` (60-page cap, _MAX_CHARS bound).
    Returns "" when the PDF has no extractable text (e.g. scanned/image-only — there
    is no OCR fallback), so callers can surface a clear error.
    """
    try:
        import io

        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        pages = [(page.extract_text() or "") for page in reader.pages[:60]]
        return "\n".join(pages).strip()[:_MAX_CHARS]
    except Exception as exc:  # noqa: BLE001 — a malformed PDF yields no text, not a crash
        logger.info("pypdf extraction from bytes failed: %s", exc)
        return ""


def _try_pypdf(pdf_url: str) -> str:
    try:
        import io

        from pypdf import PdfReader

        resp = request_with_retry("GET", pdf_url, headers={"User-Agent": _UA}, timeout=40)
        if resp.status_code != 200 or not resp.content or not resp.content[:5].startswith(b"%PDF"):
            return ""  # non-200 or an HTML challenge page (not a PDF)
        reader = PdfReader(io.BytesIO(resp.content))
        pages = []
        for page in reader.pages[:60]:  # cover long papers (MinerU is the primary path)
            pages.append(page.extract_text() or "")
        return "\n".join(pages).strip()
    except Exception as exc:  # noqa: BLE001
        logger.info("pypdf extraction failed for %s: %s", pdf_url, exc)
        return ""
