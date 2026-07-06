"""Public + settings schemas for the editable site config (singleton)."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class SiteConfigPublic(BaseModel):
    """Non-secret site settings returned by the public GET /site/config."""

    model_config = ConfigDict(from_attributes=True)
    site_name: str
    favicon_url: str
    updated_at: datetime | None = None


class SiteConfigUpdate(BaseModel):
    """Partial update — only supplied fields are written."""

    site_name: str | None = None
    favicon_url: str | None = None
