"""Pluggable paper-source registry.

Each source returns the common PaperDict shape so the discovery pipeline can
merge results from several sources. Add a new source by implementing `Source`
and registering it here.
"""
from __future__ import annotations

import logging

from app.integrations.sources.ai_paper_finder import AiPaperFinderSource
from app.integrations.sources.arxiv import ArxivSource
from app.integrations.sources.base import PaperDict, Source, SourceQuery, norm_title
from app.integrations.sources.semantic_scholar import SemanticScholarSource

logger = logging.getLogger("far.sources")

SOURCE_REGISTRY: dict[str, Source] = {
    s.key: s for s in (ArxivSource(), SemanticScholarSource(), AiPaperFinderSource())
}


def available_source_keys() -> list[str]:
    return list(SOURCE_REGISTRY.keys())


def fetch_from_sources(
    keys: list[str],
    query: SourceQuery,
    source_configs: dict[str, dict] | None = None,
    status_out: list[dict] | None = None,
    max_results_by_source: dict[str, int] | None = None,
) -> list[PaperDict]:
    """Fetch from each requested source and merge+dedup by identity.

    `source_configs` provides per-source admin config merged into the query.
    `max_results_by_source` sets a per-source target paper count (missing keys
    fall back to `query.max_results`). Unknown or failing sources are skipped
    (logged), so one bad source never breaks the run. When `status_out` is
    provided, a per-source status dict ({"source", "status", "count"/"reason"})
    is appended for each key so callers can surface partial failures in a job log.
    """
    import dataclasses

    source_configs = source_configs or {}
    max_results_by_source = max_results_by_source or {}
    merged: dict[str, PaperDict] = {}
    seen_titles: dict[str, str] = {}  # normalized title -> the merged key that owns it
    for key in keys or ["arxiv"]:
        source = SOURCE_REGISTRY.get(key)
        if source is None:
            logger.warning("unknown paper source '%s' — skipping", key)
            if status_out is not None:
                status_out.append({"source": key, "status": "unknown", "reason": "not registered"})
            continue
        per_query = dataclasses.replace(
            query,
            config={**query.config, **source_configs.get(key, {})},
            max_results=max_results_by_source.get(key) or query.max_results,
        )
        try:
            count = 0
            for paper in source.fetch(per_query):
                ident = (paper.get("id") or paper.get("title", "")).strip().lower()
                if not ident or ident in merged:
                    continue
                tkey = norm_title(paper.get("title", ""))
                if tkey and tkey in seen_titles:
                    # Same paper already kept from an earlier source (different id). Skip
                    # the duplicate, but carry over its conference label if the kept one
                    # lacks one (only AI Paper Finder knows venue/year).
                    kept = merged[seen_titles[tkey]]
                    if paper.get("venue") and not kept.get("venue"):
                        kept["venue"] = paper["venue"]
                        # Fill the year only if the kept paper has none (a present-but-
                        # empty `published`, e.g. an S2 record without a date, must still
                        # pick up the conference year — setdefault wouldn't).
                        if paper.get("published") and not kept.get("published"):
                            kept["published"] = paper["published"]
                    continue
                paper.setdefault("source", key)
                merged[ident] = paper
                if tkey:
                    seen_titles[tkey] = ident
                count += 1
            if status_out is not None:
                status_out.append({"source": key, "status": "ok", "count": count})
        except Exception as exc:  # noqa: BLE001 — never let one source break discovery
            logger.warning("source '%s' failed: %s", key, exc)
            if status_out is not None:
                status_out.append({"source": key, "status": "error", "reason": str(exc)[:200]})
    return list(merged.values())


__all__ = [
    "Source",
    "SourceQuery",
    "PaperDict",
    "SOURCE_REGISTRY",
    "available_source_keys",
    "fetch_from_sources",
]
