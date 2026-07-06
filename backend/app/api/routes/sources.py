"""Paper-source helper routes (e.g. available paperfinder venues for the UI)."""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user
from app.integrations.sources.http import request_with_retry
from app.models.user import User

router = APIRouter(prefix="/sources", tags=["sources"])


def paperfinder_base() -> str:
    """The paperfinder sidecar base URL (without /search), or "" when unconfigured."""
    endpoint = os.environ.get("PAPERFINDER_ENDPOINT", "")
    return endpoint.rsplit("/search", 1)[0].rstrip("/") if endpoint else ""


@router.get("")
def list_enabled_sources(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> list[dict]:
    """Admin-enabled paper sources for the project picker (key + display name).

    The picker offers only enabled sources so a user can't pick one the admin turned
    off (which discovery would then skip). Falls back to the built-in registry when no
    PaperSource rows are seeded yet, so the picker is never empty.
    """
    from app.models.admin import PaperSource

    rows = db.query(PaperSource).order_by(PaperSource.key).all()
    if not rows:
        from app.integrations.sources import SOURCE_REGISTRY

        return [{"key": k, "name": s.name} for k, s in SOURCE_REGISTRY.items()]
    return [{"key": r.key, "name": r.name} for r in rows if r.enabled]


@router.get("/paperfinder/venues")
def paperfinder_venues(user: User = Depends(get_current_user)) -> dict:
    """Available conference-years for the project picker, e.g.
    ``{"venues": [{"venue":"CVPR","year":"2026","count":N}, ...]}``. Only admin-ENABLED
    cohorts are offered (a disabled one is hidden + excluded from search). Empty (never an
    error) when the sidecar is unconfigured or unreachable, so the UI degrades gracefully.
    """
    base = paperfinder_base()
    if not base:
        return {"venues": []}
    try:
        resp = request_with_retry("GET", f"{base}/venues", timeout=10, max_attempts=2)
        if resp.status_code == 200:
            venues = [v for v in (resp.json().get("venues") or []) if v.get("enabled", True)]
            return {"venues": venues}
    except Exception:  # noqa: BLE001 — UI helper; never fail hard
        pass
    return {"venues": []}
