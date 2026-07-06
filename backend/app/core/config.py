"""Application settings loaded from environment.

Designed for graceful local-first operation:
- DATABASE_URL defaults to SQLite so the app runs with no external services.
- REDIS_URL is optional; when unreachable, jobs run in an in-process thread.
- OFFLINE_MODE auto-enables when no LLM provider key is configured, so the
  whole product stays demonstrable without external API keys.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo paths (this file: backend/app/core/config.py)
BACKEND_DIR = Path(__file__).resolve().parents[2]
PLATFORM_DIR = BACKEND_DIR.parent
DATA_DIR = PLATFORM_DIR / "_data"
# Reused Stage-1 pipeline (arXiv fetch + utils). Resolution order:
#   1. AUTO_RESEARCH_ROOT env (set in Docker),
#   2. vendored copy bundled in the repo (backend/vendor — self-contained deploy),
#   3. sibling Auto-Research checkout (local dev convenience).
def _resolve_auto_research_root() -> Path:
    env = os.environ.get("AUTO_RESEARCH_ROOT")
    if env:
        return Path(env).resolve()
    vendored = BACKEND_DIR / "vendor"
    if (vendored / "src").is_dir():
        return vendored.resolve()
    # No env override and no vendored copy: fall back to the vendored path anyway
    # (the Stage-1 importer degrades gracefully when its `src` dir is absent).
    return vendored.resolve()


AUTO_RESEARCH_ROOT = _resolve_auto_research_root()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=os.getenv("FAR_ENV_FILE", str(PLATFORM_DIR / ".env")),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @model_validator(mode="before")
    @classmethod
    def _ignore_empty_env(cls, data):
        """Drop empty-string env values so field defaults apply.

        `.env.example` ships keys with empty values (e.g. OFFLINE_MODE=,
        DATABASE_URL=); treating "" as 'unset' avoids bad overrides and
        Optional[bool]/[int] parse errors.
        """
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if not (isinstance(v, str) and v.strip() == "")}
        return data

    app_name: str = "Auto-Researches OSS"
    environment: str = "development"
    api_prefix: str = "/api"
    # Root for per-user/per-project artifacts. Override via DATA_ROOT env.
    data_root: str = str(DATA_DIR)

    # Security. There is no login/JWT flow, but jwt_secret must stay: the Fernet
    # key for stored credentials derives from credential_secret OR jwt_secret
    # (core/security.py) — removing it would make existing encrypted blobs
    # undecryptable.
    jwt_secret: str = "dev-insecure-change-me"
    # Fernet key for encrypting stored credentials. Auto-derived from jwt_secret
    # when unset (fine for dev; set explicitly for durable installs).
    credential_secret: Optional[str] = None

    # Database — SQLite by default; docker-compose overrides with Postgres.
    database_url: str = f"sqlite:///{(DATA_DIR / 'far.db')}"

    # Jobs / queue
    redis_url: Optional[str] = None  # e.g. redis://localhost:6379/0
    job_queue_name: str = "far"
    job_sync: bool = False  # run jobs synchronously (tests / deterministic mode)
    # How many RQ worker processes the worker container runs — i.e. how many jobs
    # process in parallel. Raise it (or scale the worker service) to cut queue wait
    # when many users/tasks are active; each concurrent job uses more CPU/RAM.
    worker_concurrency: int = 2
    # RQ worker heartbeat TTL (seconds). Far below RQ's 420s default so an idle worker
    # re-registers ~every (ttl-15)s and a worker that dies ungracefully (e.g. container
    # SIGKILL on redeploy) drops out of Redis within ~(ttl+60)s instead of ~8min — which
    # keeps the admin "live workers" count honest. Does NOT affect running jobs: a busy
    # worker heartbeats on job_monitoring_interval (30s), independent of this value.
    # Floored to 15 by _floor_worker_heartbeat_ttl (a non-positive value would break the count).
    worker_heartbeat_ttl: int = 60
    # A queued/running job whose last update is older than this is assumed dead (its
    # worker was killed mid-run) and is reaped to a terminal state on startup, so the UI
    # never shows a job "running" forever. Well above a healthy worker's progress cadence.
    stale_job_minutes: int = 30

    # CORS
    cors_origins: str = "http://localhost:5173,http://localhost:4173,http://localhost:3000"

    # LLM provider keys (optional). Presence flips OFFLINE_MODE off.
    anthropic_api_key: Optional[str] = None
    openai_api_key: Optional[str] = None
    gemini_api_key: Optional[str] = None
    deepseek_api_key: Optional[str] = None
    default_llm_provider: str = "claude"
    offline_mode: Optional[bool] = None  # explicit override

    # Local frontend origin (dev server / built SPA).
    frontend_url: str = "http://localhost:5173"

    @model_validator(mode="after")
    def _floor_worker_heartbeat_ttl(self) -> "Settings":
        """A non-positive TTL would make RQ silently fall back to its 420s default while
        the live-count freshness window (ttl*1.5) collapses to <=0 and drops every worker
        — the gauge would read 0 forever. Floor it to a sane, non-chatty minimum."""
        if self.worker_heartbeat_ttl < 15:
            self.worker_heartbeat_ttl = 15
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_offline(self) -> bool:
        """True when no real LLM key is available (or explicitly forced)."""
        if self.offline_mode is not None:
            return self.offline_mode
        return not any(
            [self.anthropic_api_key, self.openai_api_key, self.gemini_api_key, self.deepseek_api_key]
        )

    @property
    def data_dir(self) -> Path:
        path = Path(self.data_root)
        path.mkdir(parents=True, exist_ok=True)
        return path


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.data_dir  # ensure data dir exists
    return settings


settings = get_settings()
