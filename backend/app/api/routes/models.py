"""User-facing model catalog: list every enabled model.

Returns only display fields (no key, no base URL) for populating the per-step
model picker. The catalog is configured in Settings (/admin/models).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.user import User
from app.schemas.admin import ModelOption
from app.services import model_catalog

router = APIRouter(prefix="/models", tags=["models"])


@router.get("", response_model=list[ModelOption])
def list_available_models(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> list[ModelOption]:
    return [
        ModelOption(
            id=m.id, label=m.label, kind=m.kind, provider=m.provider, model=m.model,
            key_set=bool(m.api_key_enc),
            test_failed=m.last_test_ok is False,  # only ever True for admins (filtered)
            supported_efforts=m.supported_efforts or [],
        )
        for m in model_catalog.available_models(db, user.id)
    ]
