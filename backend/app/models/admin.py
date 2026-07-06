"""Admin-managed global resources: model catalog, paper sources, and integrations."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.enums import ModelKind


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ModelCatalog(Base):
    """A registered model usable for a step.

    The Settings page owns the source + key + base URL; steps only select from
    this catalog. The key is stored encrypted and never returned to clients.
    """

    __tablename__ = "model_catalog"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[ModelKind] = mapped_column(default=ModelKind.api)
    # Provider is a free-text label; it does NOT pick the client by itself.
    provider: Mapped[str] = mapped_column(String(40), default="claude")
    # The wire protocol used to call this model: "anthropic" | "openai" | "" (=infer
    # from provider). This — not `provider` — selects the Anthropic vs OpenAI client,
    # so an OpenAI-compatible third party (deepseek/glm/minimax native endpoint) works
    # by setting api_style="openai" regardless of the provider name.
    api_style: Mapped[str] = mapped_column(String(20), default="")
    base_url: Mapped[str] = mapped_column(String(300), default="")
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    api_key_enc: Mapped[str] = mapped_column(Text, default="")  # Fernet-encrypted
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Legacy SaaS column (tier gating is gone — every model is available). Kept so
    # existing rows and API payloads that still send it keep working.
    allowed_tiers: Mapped[list] = mapped_column(
        JSON, default=lambda: ["free", "pro", "max"]
    )
    # Reasoning EFFORT levels this model accepts (subset of low/medium/high/xhigh/max);
    # [] = no effort (the per-step picker offers only "off"). A requested level is
    # clamped down to the highest supported one (see enums.clamp_effort).
    supported_efforts: Mapped[list] = mapped_column(JSON, default=list)
    # Outcome of the last admin connectivity test. None = never tested (or stale:
    # editing a connection-relevant field resets it). A False here hides the model
    # from non-admin users' per-step pickers until an admin re-tests successfully.
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=None)
    last_test_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class PaperSource(Base):
    """A globally-registered paper source admins can enable/disable/configure."""

    __tablename__ = "paper_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)  # SourceKey value
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    config: Mapped[dict] = mapped_column(JSON, default=dict)  # e.g. {"api_key_env": "S2_API_KEY"}
    # A pool of API keys (Fernet-encrypted JSON list) rotated across requests to spread
    # load and avoid per-key rate limits when many users hit the source (e.g. S2).
    api_keys_enc: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class SiteConfig(Base):
    """Singleton (id=1) holding editable runtime knobs (site name/favicon plus worker
    concurrency). All fields are non-secret; the name/favicon subset is exposed by the
    public GET /api/site/config."""

    __tablename__ = "site_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    site_name: Mapped[str] = mapped_column(String(120), default="Auto-Researches OSS")
    # Holds either an external URL or an uploaded image as a base64 data URI, so it
    # must be unbounded text (a data URI easily exceeds a VARCHAR limit).
    favicon_url: Mapped[str] = mapped_column(Text, default="")
    # Admin-controlled background-worker concurrency. 0 = use the WORKER_CONCURRENCY env
    # default; >0 overrides it. The worker container's supervisor polls this and grows/
    # retires its process pool to match (gracefully) without a restart.
    worker_concurrency: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class IntegrationConfig(Base):
    """Singleton (id=1) of managed third-party integration credentials — never
    exposed publicly. Currently MinerU (PDF→markdown)."""

    __tablename__ = "integration_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mineru_api_url: Mapped[str] = mapped_column(String(500), default="")
    mineru_api_key_enc: Mapped[str] = mapped_column(Text, default="")  # Fernet-encrypted
    # Max seconds to wait for a MinerU async parse before falling back to pypdf/abstract.
    # 0 = built-in default (120 s). Raise it for slow/large PDFs (e.g. OpenReview).
    mineru_max_wait_seconds: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
