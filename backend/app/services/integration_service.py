"""Managed third-party integration credentials (singleton).

Covers the MinerU PDF→markdown API. Resolution prefers the value configured in
Settings and falls back to the matching env vars for back-compat. Keys are stored
encrypted and never returned to clients (only a `*_set` boolean is exposed)."""
from __future__ import annotations

import os

from sqlalchemy.orm import Session

from app.core.security import decrypt_secret, encrypt_secret
from app.models.admin import IntegrationConfig

INTEGRATION_ID = 1


def get_or_create(db: Session) -> IntegrationConfig:
    cfg = db.get(IntegrationConfig, INTEGRATION_ID)
    if cfg is None:
        # Flush (don't commit): this is also called during summarization while a
        # PaperDocument is pending — committing here would persist it prematurely.
        # The admin GET route and set_mineru commit explicitly.
        cfg = IntegrationConfig(id=INTEGRATION_ID)
        db.add(cfg)
        db.flush()
    return cfg


def mineru_config(db: Session) -> tuple[str, str]:
    """(api_key, api_url) for MinerU — admin config first, then env fallback."""
    cfg = get_or_create(db)
    key = decrypt_secret(cfg.mineru_api_key_enc) if cfg.mineru_api_key_enc else ""
    url = cfg.mineru_api_url or ""
    return (key or os.environ.get("MINERU_API_KEY", ""), url or os.environ.get("MINERU_API_URL", ""))


def mineru_max_wait(db: Session) -> int:
    """Admin-set max seconds to wait for a MinerU async parse (0 = built-in default).
    Admin value first, then MINERU_MAX_WAIT_SECONDS env, then 0."""
    cfg = get_or_create(db)
    if cfg.mineru_max_wait_seconds and cfg.mineru_max_wait_seconds > 0:
        return int(cfg.mineru_max_wait_seconds)
    try:
        return max(0, int(os.environ.get("MINERU_MAX_WAIT_SECONDS", "0")))
    except ValueError:
        return 0


def set_mineru(
    db: Session, *, api_url: str | None = None, api_key: str | None = None,
    max_wait_seconds: int | None = None,
) -> IntegrationConfig:
    cfg = get_or_create(db)
    if api_url is not None:
        cfg.mineru_api_url = api_url.strip()
    if api_key:  # only replace when a non-empty key is supplied
        cfg.mineru_api_key_enc = encrypt_secret(api_key.strip())
    if max_wait_seconds is not None:
        cfg.mineru_max_wait_seconds = max(0, int(max_wait_seconds))
    db.commit()
    db.refresh(cfg)
    return cfg
