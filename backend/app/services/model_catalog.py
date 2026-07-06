"""Resolve registered models for a step.

The Settings page owns the model catalog (source + key + base URL); steps only
select from it. This module centralizes per-step model lookup and key decryption
for the standard-API resolver (`model_select`). The SaaS build tier-gated this
catalog; the OSS single-user edition makes every enabled model available.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.security import decrypt_secret
from app.models.admin import ModelCatalog
from app.models.project import Project


def available_models(db: Session, user_id: int) -> list[ModelCatalog]:
    """Every enabled catalog model (for the per-step picker).

    Models whose last connectivity test failed stay listed (flagged in the UI) so
    they can be debugged from Settings.
    """
    return (
        db.query(ModelCatalog)
        .filter(ModelCatalog.enabled.is_(True))
        .order_by(ModelCatalog.id)
        .all()
    )


def model_for_step(
    db: Session, user_id: int, project: Project | None, step: str
) -> ModelCatalog | None:
    """Resolve the catalog model for a step (Channel A).

    Order: the project's explicit per-step pick (if enabled) -> the first enabled model.
    """
    step_cfg = ((project.step_models or {}).get(step, {}) if project else {}) or {}
    model_id = step_cfg.get("model_id")
    if model_id:
        entry = db.get(ModelCatalog, int(model_id))
        if entry is not None and entry.enabled:
            return entry

    # No usable explicit pick — fall back to the first enabled model.
    return (
        db.query(ModelCatalog)
        .filter(ModelCatalog.enabled.is_(True))
        .order_by(ModelCatalog.id)
        .first()
    )


def key_of(entry: ModelCatalog) -> str:
    """Decrypt and return the model's API key/token."""
    return decrypt_secret(entry.api_key_enc) if entry.api_key_enc else ""
