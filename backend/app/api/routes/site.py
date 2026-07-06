"""Public site config: name, favicon, and runtime knobs (no auth)."""
from __future__ import annotations

import hashlib

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.site import SiteConfigPublic
from app.services.site_service import get_or_create_site_config

router = APIRouter(prefix="/site", tags=["site"])


@router.get("/config", response_model=SiteConfigPublic)
def site_config(
    request: Request, response: Response, db: Session = Depends(get_db)
):
    """Public config. An ETag + `no-cache` lets the browser revalidate cheaply: an
    unchanged config returns a tiny 304 instead of re-shipping the (possibly large,
    base64-favicon) body on every page load, while Settings edits still show up at
    once because the ETag changes whenever the config does."""
    cfg = get_or_create_site_config(db)
    stamp = f"{cfg.updated_at}|{len(cfg.favicon_url or '')}"
    etag = 'W/"' + hashlib.sha256(stamp.encode()).hexdigest()[:16] + '"'
    cache = "no-cache"  # always revalidate; serve cached body only on a 304 match
    if request.headers.get("if-none-match") == etag:
        return Response(
            status_code=status.HTTP_304_NOT_MODIFIED,
            headers={"ETag": etag, "Cache-Control": cache},
        )
    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = cache
    return SiteConfigPublic.model_validate(cfg)
