"""Credential schemas. Secrets are write-only; responses are masked."""
from __future__ import annotations

from pydantic import BaseModel


class CredentialIn(BaseModel):
    provider: str  # 'zotero' (model-provider keys are admin-owned, see /admin/models)
    # Fields per provider (required fields enforced server-side, see credentials.REQUIRED_FIELDS):
    #   zotero: {"api_key": "...", "library_id": "...", "library_type": "user"}
    data: dict[str, str]


class CredentialOut(BaseModel):
    provider: str
    configured: bool
    # Masked preview of each field (never the raw secret).
    masked: dict[str, str]
