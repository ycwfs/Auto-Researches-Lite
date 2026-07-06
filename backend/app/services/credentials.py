"""Read/write encrypted per-user credentials."""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.core.security import decrypt_secret, encrypt_secret, mask_secret
from app.models.user import Credential, User

# The non-empty fields each provider needs to be genuinely usable. A credential is only
# "configured" when ALL of these are present — never merely because a row exists. Every
# status surface (list endpoint, the Settings chip, use-time guards) reads this so they
# can't disagree. `library_type` (zotero) is optional and omitted here.
REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "zotero": ("api_key", "library_id"),
}


def missing_required(provider: str, data: dict[str, str] | None) -> list[str]:
    """Required fields that are absent or blank for this provider (empty list = OK)."""
    data = data or {}
    return [f for f in REQUIRED_FIELDS.get(provider, ()) if not str(data.get(f, "")).strip()]


def merge_incoming(existing: dict[str, str] | None, incoming: dict[str, str]) -> dict[str, str]:
    """Apply an edit honoring "leave blank to keep": provided non-empty values override,
    blank/absent fields keep the stored value. This makes re-saving one changed field not
    wipe the others (the whole encrypted blob is otherwise replaced)."""
    merged = dict(existing or {})
    for key, value in incoming.items():
        if str(value).strip():  # only non-empty values overwrite
            merged[key] = value
    return merged


def is_configured(db: Session, user: User, provider: str) -> bool:
    """True only when every required field for the provider is present and non-empty."""
    return not missing_required(provider, get_credential(db, user, provider))


def set_credential(db: Session, user: User, provider: str, data: dict[str, str]) -> Credential:
    blob = encrypt_secret(json.dumps(data))
    cred = (
        db.query(Credential)
        .filter(Credential.user_id == user.id, Credential.provider == provider)
        .one_or_none()
    )
    if cred is None:
        cred = Credential(user_id=user.id, provider=provider, encrypted_blob=blob)
        db.add(cred)
    else:
        cred.encrypted_blob = blob
    db.commit()
    db.refresh(cred)
    return cred


def get_credential(db: Session, user: User, provider: str) -> dict[str, str] | None:
    cred = (
        db.query(Credential)
        .filter(Credential.user_id == user.id, Credential.provider == provider)
        .one_or_none()
    )
    if cred is None:
        return None
    raw = decrypt_secret(cred.encrypted_blob)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def masked_credential(db: Session, user: User, provider: str) -> dict[str, str]:
    data = get_credential(db, user, provider) or {}
    return {k: mask_secret(str(v)) for k, v in data.items()}
