"""AI Paper Finder source — optional HTTP discovery API (e.g. the paperfinder
sidecar doing semantic search over a curated venue corpus).

Returns nothing unless an endpoint is configured — either per-source admin
`config["endpoint"]` or the `PAPERFINDER_ENDPOINT` env var. When configured, it
queries `GET {endpoint}?q=<text>&limit=<n>` through the shared retry helper and
maps the response to PaperDicts. Any failure is logged and yields an empty list
so one bad source never breaks discovery.
"""
from __future__ import annotations

import logging
import os
from typing import Any

import requests

from app.integrations.sources.base import PaperDict, Source, SourceQuery
from app.integrations.sources.http import request_with_retry

logger = logging.getLogger("far.sources.aipf")


def _as_float(value: Any) -> float:
    """Best-effort float for the sidecar's per-paper score; 0.0 when absent/invalid."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


class AiPaperFinderSource(Source):
    key = "ai_paper_finder"
    name = "AI Paper Finder"

    def fetch(self, query: SourceQuery) -> list[PaperDict]:
        endpoint = query.config.get("endpoint") or os.environ.get("PAPERFINDER_ENDPOINT", "")
        if not endpoint:
            logger.info(
                "ai_paper_finder is not configured (no endpoint); returning no papers."
            )
            return []

        # Prefer the project's explicit AI Paper Finder query, sent VERBATIM — ideally a
        # pasted abstract, which is what makes the semantic match shine. Only when it is
        # empty (legacy projects, programmatic creation) do we fall back to composing a
        # query from keywords + the project topic (name), then categories.
        q = (query.config.get("paper_finder_query") or "").strip()
        if not q:
            parts = list(query.keywords or [])
            topic = (query.config.get("topic") or "").strip()
            if topic:
                parts.append(topic)
            if parts:
                q = " ".join(parts)
            else:
                q = " ".join(query.categories) if query.categories else "machine learning"

        api_key = os.environ.get(query.config.get("api_key_env", "AI_PAPER_FINDER_KEY"), "")
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        params: dict = {"q": q, "limit": min(query.max_results, 100)}
        # Restrict to the project's chosen conference venues (empty = all).
        venues = [v for v in (query.config.get("venues") or []) if v]
        if venues:
            params["venue"] = ",".join(venues)
        # Relevance threshold: when >0 the sidecar returns every paper scoring >= this
        # (governing retrieval) instead of a fixed top-N, so one run pulls the full
        # relevant set. 0 = off (fixed top-N by the limit above).
        try:
            min_score = float(query.config.get("paper_finder_min_score") or 0.0)
        except (TypeError, ValueError):
            min_score = 0.0
        if min_score > 0:
            params["min_score"] = min_score
        try:
            resp = request_with_retry(
                "GET", endpoint, headers=headers, params=params, timeout=30
            )
        except requests.RequestException as exc:
            logger.warning("ai_paper_finder request failed after retries: %s", exc)
            return []
        if resp.status_code != 200:
            logger.warning("ai_paper_finder returned %s", resp.status_code)
            return []
        try:
            items = resp.json()
        except ValueError:
            logger.warning("ai_paper_finder returned non-JSON body")
            return []
        if isinstance(items, dict):
            items = items.get("data") or items.get("results") or []
        return [self._to_paper(it) for it in items if isinstance(it, dict)]

    def _to_paper(self, item: dict[str, Any]) -> PaperDict:
        return {
            "id": str(item.get("id") or item.get("arxiv_id") or item.get("doi") or ""),
            "title": str(item.get("title", "") or ""),
            "authors": item.get("authors") or [],
            "abstract": str(item.get("abstract") or item.get("summary") or ""),
            "categories": item.get("categories") or item.get("fields") or [],
            "pdf_url": str(item.get("pdf_url") or item.get("url") or ""),
            "published": str(item.get("published") or item.get("year") or ""),
            # The curated conference + year (e.g. "CVPR") — shown on the discovery card.
            "venue": str(item.get("venue") or ""),
            # Semantic similarity (cosine) the sidecar scored this paper at — the value the
            # relevance threshold filtered on. Carried through so it can be stored + shown.
            "finder_score": _as_float(item.get("score")),
            "source": self.key,
        }
