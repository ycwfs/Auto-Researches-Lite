"""Relevance retrieval over a paper set — semantic-first, with graceful fallback.

`rank_relevant` ranks papers against a query by, in order of preference:
  1. embedding cosine similarity (true semantic; when an OpenAI key resolves),
  2. TF-IDF cosine (lexical vector space; offline, deterministic, sklearn),
  3. keyword-overlap counting (last resort if sklearn is unavailable).

This replaces a plain substring keyword count, which misses paraphrase/synonyms
and can't rank (it only counts exact-substring hits).
"""
from __future__ import annotations

import logging
import math
from collections.abc import Callable
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.services import embeddings

logger = logging.getLogger("far.retrieval")


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _tfidf_scores(query: str, docs: list[str]) -> Optional[list[float]]:
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity

        vec = TfidfVectorizer(stop_words="english", max_features=8192)
        matrix = vec.fit_transform([query, *docs])
        sims = cosine_similarity(matrix[0:1], matrix[1:]).ravel()
        return [float(s) for s in sims]
    except Exception as exc:  # noqa: BLE001
        logger.warning("tf-idf scoring failed: %s", exc)
        return None


def _keyword_scores(query: str, docs: list[str]) -> list[float]:
    terms = {w for w in query.lower().replace(",", " ").split() if len(w) > 2}
    return [float(sum(1 for t in terms if t in d.lower())) for d in docs]


def rank_relevant(
    query: str,
    papers: list[Any],
    *,
    text_of: Callable[[Any], str],
    top_k: int,
    db: Session | None = None,
) -> tuple[list[Any], str]:
    """Return (papers ranked by relevance to `query`, truncated to top_k; method).

    `method` is one of: "embeddings", "tfidf", "keyword", "none".
    """
    if not papers:
        return [], "none"
    if not query.strip():
        return papers[:top_k], "none"

    docs = [text_of(p) for p in papers]
    scores: Optional[list[float]] = None
    method = "none"

    vecs = embeddings.embed_texts([query, *docs], db=db)
    if vecs and len(vecs) == len(docs) + 1:
        qv, dvs = vecs[0], vecs[1:]
        scores = [_cosine(qv, dv) for dv in dvs]
        method = "embeddings"

    if scores is None:
        scores = _tfidf_scores(query, docs)
        if scores is not None:
            method = "tfidf"

    if scores is None:
        scores = _keyword_scores(query, docs)
        method = "keyword"

    # Sort by score only (key= never compares the paper objects, so dicts are safe).
    pairs = sorted(zip(scores, papers), key=lambda pair: pair[0], reverse=True)
    # When there's a real signal, drop clearly-irrelevant (score<=0) items so obvious
    # noise isn't fed downstream; if nothing matches at all, keep the full capped set
    # (the old whole-library fallback) so grounding is never empty.
    if pairs and pairs[0][0] > 0:
        kept = [p for s, p in pairs if s > 0]
    else:
        kept = [p for _, p in pairs]
    return kept[:top_k], method
