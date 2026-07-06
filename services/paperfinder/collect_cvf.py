"""Collect CVF Open Access papers (CVPR / ICCV / WACV) as normalized JSONL for
build_corpus.py. CVF venues are NOT on OpenReview's accepted path, so this scrapes
openaccess.thecvf.com directly: parse the all-days listing for paper pages, then
fetch each page for its abstract (concurrently).

  python collect_cvf.py --venue CVPR --year 2026 > papers.jsonl
  python collect_cvf.py --venue CVPR --year 2026 | python build_corpus.py

Each emitted line: {"id","title","abstract","pdf","venue","year"} — the shape
build_corpus.py expects. Papers without an extractable abstract are skipped (the
embedder needs text).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor

import requests

BASE = "https://openaccess.thecvf.com"
# <dt class="ptitle"><br><a href="/content/CVPR2026/html/..._paper.html">Title</a></dt>
_ENTRY = re.compile(
    r'<dt class="ptitle">\s*(?:<br\s*/?>)?\s*<a href="(/content/[^"]+\.html)">(.*?)</a>\s*</dt>',
    re.S,
)
_ABSTRACT = re.compile(r'<div id="abstract"[^>]*>(.*?)</div>', re.S)
_TAGS = re.compile(r"<[^>]+>")


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", _TAGS.sub("", s)).strip()


def _list_papers(venue: str, year: str, session: requests.Session) -> list[tuple[str, str]]:
    html = session.get(f"{BASE}/{venue}{year}?day=all", timeout=90).text
    seen, out = set(), []
    for m in _ENTRY.finditer(html):
        link, title = m.group(1), _clean(m.group(2))
        if title and link not in seen:
            seen.add(link)
            out.append((link, title))
    return out


def _fetch_abstract(link: str, session: requests.Session) -> str:
    try:
        m = _ABSTRACT.search(session.get(BASE + link, timeout=30).text)
        return _clean(m.group(1)) if m else ""
    except requests.RequestException:
        return ""


def collect(venue: str, year, *, workers: int = 12) -> list[dict]:
    """Collect normalized CVF paper records for a venue/year (importable: used by the
    sidecar's in-process ingest as well as the CLI). Only records with an abstract."""
    session = requests.Session()
    session.headers["User-Agent"] = "paperfinder-corpus/1.0 (+research index)"
    papers = _list_papers(venue, str(year), session)

    def _one(item: tuple[str, str]) -> dict:
        link, title = item
        pdf = BASE + link.replace("/html/", "/papers/").replace(".html", ".pdf")
        rid = link.rsplit("/", 1)[-1].replace("_paper.html", "")
        return {
            "id": rid, "title": title, "abstract": _fetch_abstract(link, session),
            "pdf": pdf, "venue": venue, "year": str(year),
        }

    out: list[dict] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for rec in pool.map(_one, papers):
            if rec["abstract"]:  # the embedder needs text
                out.append(rec)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Collect CVF Open Access papers as JSONL.")
    ap.add_argument("--venue", required=True, help="CVPR | ICCV | WACV")
    ap.add_argument("--year", required=True)
    ap.add_argument("--workers", type=int, default=12, help="concurrent abstract fetches")
    args = ap.parse_args()

    records = collect(args.venue, args.year, workers=args.workers)
    for rec in records:
        print(json.dumps(rec, ensure_ascii=False))
    print(f"# collected {len(records)} {args.venue} {args.year} papers (with abstracts)", file=sys.stderr)


if __name__ == "__main__":
    main()
