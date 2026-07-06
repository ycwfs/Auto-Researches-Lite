"""Text embeddings via OpenAI, with graceful absence.

`embed_texts` returns None whenever embeddings aren't available — explicit
OFFLINE_MODE, no resolvable key, or any API error — so callers fall back to a
non-embedding retriever. The key is resolved from settings.OPENAI_API_KEY or,
failing that, an enabled OpenAI-provider model in the admin catalog (the same
key the admin already trusted the app with). Embeddings are read-only.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.core.config import settings

logger = logging.getLogger("far.embeddings")

_MODEL = "text-embedding-3-small"
_MAX_CHARS = 1200  # bound tokens per input
_BATCH = 128  # inputs per request


def _resolve_key(db: Session | None) -> tuple[str | None, str | None]:
    """(api_key, base_url) for an OpenAI-compatible embeddings endpoint, or (None, None)."""
    if settings.openai_api_key:
        return settings.openai_api_key, None
    if db is not None:
        try:
            from app.models.admin import ModelCatalog
            from app.services import model_catalog

            entry = (
                db.query(ModelCatalog)
                .filter(ModelCatalog.enabled.is_(True), ModelCatalog.provider == "openai")
                .order_by(ModelCatalog.id)
                .first()
            )
            if entry:
                key = model_catalog.key_of(entry)
                if key:
                    return key, (entry.base_url or None)
        except Exception:  # noqa: BLE001 — best-effort resolution
            return None, None
    return None, None


def available(db: Session | None = None) -> bool:
    """True when a real embeddings call would be attempted."""
    if settings.offline_mode is True:
        return False
    key, _ = _resolve_key(db)
    return bool(key)


def embed_texts(texts: list[str], db: Session | None = None) -> list[list[float]] | None:
    """Embed `texts`; None signals 'fall back to a non-embedding retriever'."""
    if settings.offline_mode is True or not texts:
        return None
    key, base_url = _resolve_key(db)
    if not key:
        return None
    try:
        from openai import OpenAI

        client = OpenAI(api_key=key, **({"base_url": base_url} if base_url else {}))
        out: list[list[float]] = []
        for i in range(0, len(texts), _BATCH):
            chunk = [((t or "").strip()[:_MAX_CHARS] or " ") for t in texts[i : i + _BATCH]]
            resp = client.embeddings.create(model=_MODEL, input=chunk)
            out.extend(d.embedding for d in resp.data)
        return out if len(out) == len(texts) else None
    except Exception as exc:  # noqa: BLE001 — any failure → fall back
        logger.warning("embeddings unavailable, falling back: %s", exc)
        return None
