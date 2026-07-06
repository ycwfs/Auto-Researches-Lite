"""Admin schemas: model catalog, paper sources, integrations."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import _EFFORT_ORDER, ModelKind


def _strip(v: object) -> object:
    """Trim copy-paste whitespace so provider/model/base_url don't carry stray spaces."""
    return v.strip() if isinstance(v, str) else v


def _clean_efforts(v: list[str]) -> list[str]:
    """Keep only valid effort levels (drop 'off'/unknown), deduped in canonical order."""
    seen = {str(x).strip().lower() for x in (v or [])}
    return [e for e in _EFFORT_ORDER if e in seen]


class PaperSourceIn(BaseModel):
    key: str
    name: str
    description: str = ""
    enabled: bool = True
    config: dict = {}
    # API key pool (stored encrypted, never returned). Rotated to avoid rate limits.
    api_keys: list[str] = []


class PaperSourceUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    enabled: bool | None = None
    config: dict | None = None
    # When provided, replaces the key pool (empty list clears it); None leaves it.
    api_keys: list[str] | None = None


class PaperSourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    key: str
    name: str
    description: str
    enabled: bool
    config: dict
    key_count: int = 0  # number of stored API keys (the keys themselves are never returned)


# Legacy SaaS tier labels. Tier gating is gone (every model is available); the
# field is still accepted so stored rows and older clients keep working.
DEFAULT_TIERS = ["free", "pro", "max"]


class ModelCatalogIn(BaseModel):
    label: str
    kind: ModelKind = ModelKind.api
    provider: str = "claude"
    api_style: str = ""  # "anthropic" | "openai" | "" (=infer from provider)
    base_url: str = ""
    model: str
    api_key: str = ""  # write-only; encrypted at rest
    enabled: bool = True
    allowed_tiers: list[str] = DEFAULT_TIERS
    # Effort levels this model supports; [] = no effort. Sanitized to valid levels.
    supported_efforts: list[str] = Field(default_factory=list)

    _trim = field_validator("label", "provider", "api_style", "base_url", "model", "api_key", mode="before")(_strip)

    @field_validator("label", "model", "provider")
    @classmethod
    def _require_nonempty(cls, v: str) -> str:
        if not (v or "").strip():
            raise ValueError("must not be blank")
        return v

    @field_validator("supported_efforts")
    @classmethod
    def _valid_efforts(cls, v: list[str]) -> list[str]:
        return _clean_efforts(v)


class ModelCatalogUpdate(BaseModel):
    label: str | None = None
    kind: ModelKind | None = None
    provider: str | None = None
    api_style: str | None = None
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None  # when non-empty, replaces the stored key
    enabled: bool | None = None
    allowed_tiers: list[str] | None = None
    supported_efforts: list[str] | None = None

    _trim = field_validator("label", "provider", "api_style", "base_url", "model", "api_key", mode="before")(_strip)

    @field_validator("supported_efforts")
    @classmethod
    def _valid_efforts(cls, v: list[str] | None) -> list[str] | None:
        return None if v is None else _clean_efforts(v)

    @field_validator("label", "model", "provider")
    @classmethod
    def _reject_blank_when_set(cls, v: str | None) -> str | None:
        # A field the UI treats as required must not be blanked via the API on an update.
        if v is not None and not v.strip():
            raise ValueError("must not be blank")
        return v


class ModelCatalogOut(BaseModel):
    """Admin view — includes whether a key is set + a mask, never the raw key."""

    model_config = ConfigDict(from_attributes=True)
    id: int
    label: str
    kind: ModelKind
    provider: str
    api_style: str = ""  # "" when unset (the client is then inferred from provider)
    base_url: str
    model: str
    enabled: bool
    allowed_tiers: list[str]
    supported_efforts: list[str] = Field(default_factory=list)
    key_set: bool
    # Last connectivity-test outcome: True/False, or None = never tested (also reset
    # when a connection-relevant field is edited).
    last_test_ok: bool | None = None
    last_test_at: datetime | None = None


class ModelOption(BaseModel):
    """User-facing view for the per-step picker — no key, no base_url."""

    id: int
    label: str
    kind: ModelKind
    provider: str
    model: str
    key_set: bool = False  # False → no API key, this pick silently runs the offline mock
    # True → the admin's last connectivity test failed. Non-admins never receive such
    # models (filtered server-side); admins see them flagged so they can debug.
    test_failed: bool = False
    # Effort levels this model supports — the per-step picker offers only these (+ off).
    supported_efforts: list[str] = Field(default_factory=list)


class IntegrationConfigOut(BaseModel):
    """Admin view of third-party integration config — never returns raw keys."""

    mineru_api_url: str = ""
    mineru_key_set: bool = False
    mineru_max_wait_seconds: int = 0  # 0 = built-in default (120 s)


class IntegrationConfigUpdate(BaseModel):
    mineru_api_url: str | None = None
    mineru_api_key: str | None = None  # write-only; replaces the stored key when non-empty
    mineru_max_wait_seconds: int | None = Field(default=None, ge=0, le=3600)

    _trim = field_validator("mineru_api_url", "mineru_api_key", mode="before")(_strip)


class ApiTestResult(BaseModel):
    ok: bool
    detail: str
