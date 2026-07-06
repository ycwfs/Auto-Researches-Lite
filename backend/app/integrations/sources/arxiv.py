"""arXiv source — wraps the reused Auto-Research fetcher.

arXiv egress can be intermittently slow/flaky (Fastly CDN). The `arxiv` library
sets no per-request timeout, so without one a stalled read hangs for minutes. We
pin a process socket timeout for the duration of the fetch (restored afterward)
so each HTTP read fails fast; combined with the fetcher's bounded retries, a dead
source raises within seconds and `fetch_from_sources` isolates it.
"""
from __future__ import annotations

import os
import socket
from pathlib import Path
from tempfile import mkdtemp

from app.integrations.auto_research import build_pipeline_config, fetch_arxiv_papers
from app.integrations.sources.base import PaperDict, Source, SourceQuery


class ArxivSource(Source):
    key = "arxiv"
    name = "arXiv"

    def fetch(self, query: SourceQuery) -> list[PaperDict]:
        papers_dir = Path(query.config.get("papers_dir") or mkdtemp(prefix="arxiv-"))
        # The project name (topic) joins keywords as a title/abstract term, so the
        # arXiv search is driven by name + categories (build_query ANDs categories
        # with the OR of these terms).
        topic = (query.config.get("topic") or "").strip()
        keywords = list(query.keywords or [])
        if topic and topic not in keywords:
            keywords.append(topic)
        config = build_pipeline_config(
            categories=query.categories,
            keywords=keywords,
            max_results=query.max_results,
            papers_dir=papers_dir,
        )
        timeout = float(os.environ.get("ARXIV_HTTP_TIMEOUT", "12"))
        previous = socket.getdefaulttimeout()
        socket.setdefaulttimeout(timeout)
        try:
            return fetch_arxiv_papers(config, days_back=query.days_back)
        finally:
            socket.setdefaulttimeout(previous)
