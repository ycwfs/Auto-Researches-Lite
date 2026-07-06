"""Credential management routes (set / list). Secrets never returned raw."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.user import Credential, User
from app.schemas.credential import CredentialIn, CredentialOut
from app.services.credentials import (
    get_credential,
    is_configured,
    masked_credential,
    merge_incoming,
    missing_required,
    set_credential,
)

router = APIRouter(prefix="/credentials", tags=["credentials"])

# Model provider keys live in the model catalog (see /admin/models); only
# integration credentials are managed here.
_KNOWN_PROVIDERS = {"zotero"}


@router.get("", response_model=list[CredentialOut])
def list_credentials(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> list[CredentialOut]:
    rows = {c.provider for c in db.query(Credential).filter(Credential.user_id == user.id).all()}
    out: list[CredentialOut] = []
    for provider in sorted(_KNOWN_PROVIDERS):
        # "configured" means the required fields are actually present — NOT merely that a
        # row exists (a blank/partial save must not show as connected).
        out.append(
            CredentialOut(
                provider=provider,
                configured=is_configured(db, user, provider),
                masked=masked_credential(db, user, provider) if provider in rows else {},
            )
        )
    return out


@router.put("", response_model=CredentialOut)
def upsert_credential(
    payload: CredentialIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CredentialOut:
    if payload.provider not in _KNOWN_PROVIDERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown credential provider '{payload.provider}'.",
        )
    # Merge onto the stored credential (honor "leave blank to keep"), then require every
    # field the provider needs — so a blank/partial save is rejected instead of being
    # persisted and shown as "connected".
    merged = merge_incoming(get_credential(db, user, payload.provider), payload.data)
    missing = missing_required(payload.provider, merged)
    if missing:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Missing required fields for {payload.provider}: {', '.join(missing)}.",
        )
    set_credential(db, user, payload.provider, merged)
    return CredentialOut(
        provider=payload.provider,
        configured=is_configured(db, user, payload.provider),
        masked=masked_credential(db, user, payload.provider),
    )
