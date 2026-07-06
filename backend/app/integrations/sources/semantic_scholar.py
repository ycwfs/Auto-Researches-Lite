"""Semantic Scholar source (free Graph API; optional S2_API_KEY for higher limits).

The relevance-search endpoint returns by relevance with no date bound, so a naive
query surfaces old, off-topic papers. We therefore: (1) build real query text —
project keywords, else the project topic, else arXiv categories mapped to plain
topic phrases (never raw codes like "cs.AI"); (2) bound results to recent
publications; (3) filter by field of study; (4) keep the precise publicationDate.
All bounds are env-tunable (see deploy/.env.example).
"""
from __future__ import annotations

import os
import random
from datetime import datetime, timedelta, timezone

import requests

from app.integrations.sources.base import PaperDict, Source, SourceQuery
from app.integrations.sources.http import request_with_retry

_SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
_FIELDS = "title,abstract,year,publicationDate,authors,externalIds,openAccessPdf,fieldsOfStudy"

# arXiv category code -> human topic phrase (used as query text when a project
# has no keywords/topic). Falls back to the prefix or "machine learning".
_CATEGORY_TOPIC = {
    "cs.AI": "artificial intelligence",
    "cs.LG": "machine learning",
    "cs.CL": "natural language processing",
    "cs.CV": "computer vision",
    "cs.NE": "neural networks",
    "cs.RO": "robotics",
    "cs.IR": "information retrieval",
    "stat.ML": "machine learning",
    "eess.AS": "speech processing",
    "eess.IV": "image processing",
}

# arXiv category prefix -> Semantic Scholar fieldsOfStudy taxonomy value.
_PREFIX_FIELD = {
    "cs": "Computer Science",
    "stat": "Mathematics",
    "math": "Mathematics",
    "eess": "Engineering",
    "physics": "Physics",
    "q-bio": "Biology",
    "econ": "Economics",
}


def _query_text(query: SourceQuery) -> str:
    """Combine keywords + the project topic (name); fall back to category phrases."""
    parts = list(query.keywords or [])
    topic = (query.config.get("topic") or "").strip()
    if topic:
        parts.append(topic)
    if parts:
        return " ".join(parts)
    phrases = [_CATEGORY_TOPIC.get(c, "") for c in (query.categories or [])]
    phrases = [p for p in phrases if p]
    return " ".join(phrases) or "machine learning"


def _fields_of_study(categories: list[str]) -> str:
    """Map arXiv categories to a comma-joined S2 fieldsOfStudy filter."""
    fields = {
        _PREFIX_FIELD[c.split(".")[0]]
        for c in (categories or [])
        if c.split(".")[0] in _PREFIX_FIELD
    }
    return ",".join(sorted(fields))


def _cfg_int(config: dict, key: str, env: str, default: int) -> int:
    """Resolve an int setting: per-user config > env > hardcoded default."""
    for raw in (config.get(key), os.environ.get(env)):
        if raw not in (None, ""):
            try:
                return int(raw)
            except (TypeError, ValueError):
                pass
    return default


def _cfg_str(config: dict, key: str, env: str) -> str | None:
    """Resolve a string setting: per-user config > env (None if neither set)."""
    if config.get(key) is not None:
        return str(config[key])
    return os.environ.get(env)


def _resolve_fields_of_study(raw: str | None, categories: list[str]) -> str:
    """unset / "" / "auto" -> derive from categories; "off"/"none" -> disabled."""
    if raw is None:
        return _fields_of_study(categories)
    token = raw.strip().lower()
    if token in ("", "auto"):
        return _fields_of_study(categories)
    if token in ("off", "none"):
        return ""
    return raw.strip()


class SemanticScholarSource(Source):
    key = "semantic_scholar"
    name = "Semantic Scholar"

    def fetch(self, query: SourceQuery) -> list[PaperDict]:
        # Settings resolve per-user (SourceQuery.config, set by discovery) first,
        # then the global env default, then the hardcoded default.
        config = query.config or {}
        # Key pool: the admin-managed keys (rotated to spread load + dodge per-key rate
        # limits across many users) plus the legacy env key as a fallback.
        pool = [k for k in (config.get("api_keys") or []) if k]
        env_key = os.environ.get(config.get("api_key_env", "S2_API_KEY"), "")
        if env_key and env_key not in pool:
            pool.append(env_key)
        random.shuffle(pool)  # spread requests across the pool
        params: dict = {
            "query": _query_text(query),
            "limit": min(query.max_results, 100),
            "fields": _FIELDS,
        }

        # Recency: restrict to papers published within the last N days (default 1y).
        recency_days = _cfg_int(config, "s2_recency_days", "S2_RECENCY_DAYS", 365)
        if recency_days > 0:
            since = (datetime.now(timezone.utc).date() - timedelta(days=recency_days)).isoformat()
            params["publicationDateOrYear"] = f"{since}:"

        fos = _resolve_fields_of_study(
            _cfg_str(config, "s2_fields_of_study", "S2_FIELDS_OF_STUDY"), query.categories
        )
        if fos:
            params["fieldsOfStudy"] = fos

        min_citations = _cfg_int(config, "s2_min_citations", "S2_MIN_CITATIONS", 0)
        if min_citations > 0:
            params["minCitationCount"] = min_citations

        data = self._get_with_backoff(pool, params)
        papers: list[PaperDict] = []
        for item in data.get("data", []) or []:
            ext = item.get("externalIds") or {}
            arxiv_id = ext.get("ArXiv", "")
            paper_id = arxiv_id or item.get("paperId", "")
            pdf = (item.get("openAccessPdf") or {}).get("url", "")
            papers.append(
                {
                    "id": paper_id,
                    "title": item.get("title", "") or "",
                    "authors": [a.get("name", "") for a in item.get("authors", []) or []],
                    "abstract": item.get("abstract", "") or "",
                    "categories": item.get("fieldsOfStudy", []) or [],
                    "pdf_url": pdf or (f"https://arxiv.org/pdf/{arxiv_id}" if arxiv_id else ""),
                    # Prefer the precise date; fall back to the year.
                    "published": item.get("publicationDate") or str(item.get("year", "") or ""),
                    "source": self.key,
                }
            )
        return papers

    def _get_with_backoff(self, keys: list[str], params: dict) -> dict:
        """Fetch, rotating across the key pool on rate-limits: try each key (with the
        shared 429/5xx + network backoff); on a persistent 429, move to the next key so
        one key's limit doesn't sink the request when others have headroom. Anonymous
        (no key) is one attempt. Raises a clear error if every key is rate-limited.
        """
        attempts = keys or [""]  # [""] = anonymous (no key)
        last_429: RuntimeError | None = None
        last_err: RuntimeError | None = None
        for i, key in enumerate(attempts):
            headers = {"x-api-key": key} if key else {}
            try:
                resp = request_with_retry(
                    "GET", _SEARCH_URL, headers=headers, params=params,
                    timeout=20, base_delay=3.0,
                    # Fail fast per key when there are others to rotate to.
                    max_attempts=2 if len(attempts) > 1 else 4,
                )
            except requests.RequestException as exc:
                last_err = RuntimeError(f"network error: {exc}")
                continue
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 429:
                last_429 = RuntimeError("rate-limited (HTTP 429)")
                continue  # try the next key in the pool
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:120]}")
        if last_429 is not None:
            hint = "" if any(attempts) else " — add a free S2 API key in the admin panel for higher limits"
            raise RuntimeError(f"rate-limited (HTTP 429) on all {len(attempts)} key(s){hint}")
        raise last_err or RuntimeError("no response from Semantic Scholar")
