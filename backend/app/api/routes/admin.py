"""Settings backend routes (kept at /api/admin/* for URL stability; the single
local user is always the admin, so there is no gating)."""
from __future__ import annotations

import base64
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import require_admin
from app.integrations.sources.http import request_with_retry
from app.core.security import decrypt_secret, encrypt_secret
from app.models.admin import ModelCatalog, PaperSource
from app.schemas.admin import (
    ApiTestResult,
    IntegrationConfigOut,
    IntegrationConfigUpdate,
    ModelCatalogIn,
    ModelCatalogOut,
    ModelCatalogUpdate,
    PaperSourceIn,
    PaperSourceOut,
    PaperSourceUpdate,
)
from app.schemas.site import SiteConfigPublic, SiteConfigUpdate
from app.services import api_test, integration_service
from app.services.site_service import get_or_create_site_config


def _integration_out(cfg) -> IntegrationConfigOut:
    return IntegrationConfigOut(
        mineru_api_url=cfg.mineru_api_url or "",
        mineru_key_set=bool(cfg.mineru_api_key_enc),
        mineru_max_wait_seconds=int(cfg.mineru_max_wait_seconds or 0),
    )

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


def _model_out(entry: ModelCatalog) -> ModelCatalogOut:
    return ModelCatalogOut(
        id=entry.id,
        label=entry.label,
        kind=entry.kind,
        provider=entry.provider,
        api_style=entry.api_style or "",  # NULL on rows predating the column
        base_url=entry.base_url,
        model=entry.model,
        enabled=entry.enabled,
        allowed_tiers=entry.allowed_tiers or [],
        supported_efforts=entry.supported_efforts or [],
        key_set=bool(entry.api_key_enc),
        last_test_ok=entry.last_test_ok,
        last_test_at=entry.last_test_at,
    )


# ---- Model catalog ---------------------------------------------------------
@router.get("/models", response_model=list[ModelCatalogOut])
def list_models(db: Session = Depends(get_db)) -> list[ModelCatalogOut]:
    rows = db.query(ModelCatalog).order_by(ModelCatalog.id).all()
    return [_model_out(m) for m in rows]


@router.post("/models", response_model=ModelCatalogOut, status_code=201)
def create_model(payload: ModelCatalogIn, db: Session = Depends(get_db)) -> ModelCatalogOut:
    data = payload.model_dump()
    api_key = data.pop("api_key", "") or ""
    entry = ModelCatalog(**data, api_key_enc=encrypt_secret(api_key) if api_key else "")
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return _model_out(entry)


@router.patch("/models/{model_id}", response_model=ModelCatalogOut)
def update_model(
    model_id: int, payload: ModelCatalogUpdate, db: Session = Depends(get_db)
) -> ModelCatalogOut:
    entry = db.get(ModelCatalog, model_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Model not found")
    data = payload.model_dump(exclude_unset=True)
    api_key = data.pop("api_key", None)
    # Changing anything connection-relevant makes the stored test outcome stale —
    # reset it to "never tested" so a previous ✗ doesn't keep hiding a now-fixed
    # model (and a previous ✓ doesn't vouch for an untested endpoint).
    connection_changed = bool(api_key) or any(
        k in data and getattr(entry, k) != data[k]
        for k in ("model", "base_url", "provider", "api_style")
    )
    if api_key:  # only replace when a non-empty key is supplied
        entry.api_key_enc = encrypt_secret(api_key)
    for k, v in data.items():
        setattr(entry, k, v)
    if connection_changed:
        entry.last_test_ok = None
        entry.last_test_at = None
    db.commit()
    db.refresh(entry)
    return _model_out(entry)


@router.delete("/models/{model_id}", status_code=204)
def delete_model(model_id: int, db: Session = Depends(get_db)) -> None:
    entry = db.get(ModelCatalog, model_id)
    if entry:
        db.delete(entry)
        db.commit()


@router.post("/models/{model_id}/test", response_model=ApiTestResult)
def test_model(model_id: int, db: Session = Depends(get_db)) -> ApiTestResult:
    entry = db.get(ModelCatalog, model_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Model not found")
    ok, detail = api_test.test_model(db, entry)
    # Persist the outcome: the per-step picker hides ✗ models from non-admin users
    # and the admin table shows the last known state across reloads.
    entry.last_test_ok = ok
    entry.last_test_at = datetime.now(timezone.utc)
    db.commit()
    return ApiTestResult(ok=ok, detail=detail)


# ---- Integrations (MinerU, …) ----------------------------------------------
@router.get("/integrations", response_model=IntegrationConfigOut)
def get_integrations(db: Session = Depends(get_db)) -> IntegrationConfigOut:
    cfg = integration_service.get_or_create(db)
    db.commit()  # persist the singleton on first read (get_or_create only flushes)
    return _integration_out(cfg)


@router.put("/integrations", response_model=IntegrationConfigOut)
def update_integrations(
    payload: IntegrationConfigUpdate, db: Session = Depends(get_db)
) -> IntegrationConfigOut:
    cfg = integration_service.set_mineru(
        db, api_url=payload.mineru_api_url, api_key=payload.mineru_api_key,
        max_wait_seconds=payload.mineru_max_wait_seconds,
    )
    return _integration_out(cfg)


@router.post("/integrations/mineru/test", response_model=ApiTestResult)
def test_mineru(db: Session = Depends(get_db)) -> ApiTestResult:
    ok, detail = api_test.test_mineru(db)
    return ApiTestResult(ok=ok, detail=detail)


# ---- Paper sources ---------------------------------------------------------
def _encode_keys(api_keys: list[str]) -> str:
    cleaned = [k.strip() for k in (api_keys or []) if k and k.strip()]
    return encrypt_secret(json.dumps(cleaned)) if cleaned else ""


def decode_source_keys(enc: str) -> list[str]:
    """Decrypt a PaperSource.api_keys_enc back into the key list (used by discovery)."""
    if not enc:
        return []
    try:
        data = json.loads(decrypt_secret(enc) or "[]")
    except (ValueError, TypeError):
        return []
    return [str(k) for k in data if k]


def _source_out(src: PaperSource) -> PaperSourceOut:
    out = PaperSourceOut.model_validate(src)
    out.key_count = len(decode_source_keys(src.api_keys_enc or ""))
    return out


@router.get("/sources", response_model=list[PaperSourceOut])
def list_sources(db: Session = Depends(get_db)) -> list[PaperSourceOut]:
    return [_source_out(s) for s in db.query(PaperSource).order_by(PaperSource.id).all()]


@router.post("/sources", response_model=PaperSourceOut, status_code=201)
def create_source(payload: PaperSourceIn, db: Session = Depends(get_db)) -> PaperSourceOut:
    if db.query(PaperSource).filter(PaperSource.key == payload.key).first():
        raise HTTPException(status_code=409, detail="Source key already exists")
    data = payload.model_dump(exclude={"api_keys"})
    src = PaperSource(**data, api_keys_enc=_encode_keys(payload.api_keys))
    db.add(src)
    db.commit()
    db.refresh(src)
    return _source_out(src)


@router.patch("/sources/{source_id}", response_model=PaperSourceOut)
def update_source(
    source_id: int, payload: PaperSourceUpdate, db: Session = Depends(get_db)
) -> PaperSourceOut:
    src = db.get(PaperSource, source_id)
    if src is None:
        raise HTTPException(status_code=404, detail="Source not found")
    data = payload.model_dump(exclude_unset=True)
    api_keys = data.pop("api_keys", None)
    for k, v in data.items():
        setattr(src, k, v)
    if api_keys is not None:  # explicit (possibly empty) list -> replace the pool
        src.api_keys_enc = _encode_keys(api_keys)
    db.commit()
    db.refresh(src)
    return _source_out(src)


@router.delete("/sources/{source_id}", status_code=204)
def delete_source(source_id: int, db: Session = Depends(get_db)) -> None:
    src = db.get(PaperSource, source_id)
    if src:
        db.delete(src)
        db.commit()


# ---- AI Paper Finder conferences (proxy the sidecar, which owns the corpus) ---
class ConferenceIngestIn(BaseModel):
    venue: str
    year: str
    source: str = "openreview"  # "openreview" (ICLR/ICML/NeurIPS) | "cvf" (CVPR/ICCV/WACV)


class ConferenceToggleIn(BaseModel):
    venue: str
    year: str
    enabled: bool


def _paperfinder(method: str, path: str, *, json_body: dict | None = None, timeout: int = 30) -> dict:
    """Call the paperfinder sidecar and surface its status/detail. The sidecar is the
    single owner of the corpus (the backend/worker can't touch its volume), so add/
    delete/toggle all go over HTTP to it."""
    from app.api.routes.sources import paperfinder_base

    base = paperfinder_base()
    if not base:
        raise HTTPException(status_code=503, detail="AI Paper Finder sidecar is not configured.")
    try:
        resp = request_with_retry(method, f"{base}{path}", json=json_body, timeout=timeout, max_attempts=2)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Paper Finder unreachable: {exc}")
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail")
        except Exception:  # noqa: BLE001
            detail = resp.text[:200]
        raise HTTPException(status_code=resp.status_code, detail=detail or "Paper Finder error")
    return resp.json() if resp.text else {}


@router.get("/conferences")
def list_conferences() -> dict:
    """All conference-years in the corpus with per-year counts + enabled flags."""
    return _paperfinder("GET", "/venues", timeout=15)


@router.post("/conferences", status_code=202)
def add_conference(body: ConferenceIngestIn) -> dict:
    """Start a background ingest (collect + embed) of a conference-year. Returns a task id
    the admin polls; the ingest runs inside the sidecar so there is no service stop/start."""
    return _paperfinder(
        "POST", "/admin/ingest",
        json_body={"venue": body.venue.strip(), "year": str(body.year).strip(), "source": body.source},
        timeout=20,
    )


@router.get("/conferences/ingest/{task_id}")
def conference_ingest_status(task_id: str) -> dict:
    return _paperfinder("GET", f"/admin/ingest/{task_id}", timeout=15)


@router.delete("/conferences/{venue}/{year}")
def delete_conference(venue: str, year: str) -> dict:
    """Remove every paper for a (venue, year) from the corpus."""
    return _paperfinder("DELETE", f"/admin/conferences/{venue}/{year}", timeout=60)


@router.patch("/conferences")
def toggle_conference(body: ConferenceToggleIn) -> dict:
    """Enable/disable a conference-year for search (data kept; hidden from pickers)."""
    return _paperfinder(
        "POST", "/admin/toggle",
        json_body={"venue": body.venue, "year": str(body.year), "enabled": body.enabled},
        timeout=15,
    )


# ---- Site config (singleton) -----------------------------------------------
@router.get("/site-config", response_model=SiteConfigPublic)
def get_site_config(db: Session = Depends(get_db)) -> SiteConfigPublic:
    return get_or_create_site_config(db)


@router.put("/site-config", response_model=SiteConfigPublic)
def update_site_config(
    payload: SiteConfigUpdate, db: Session = Depends(get_db)
) -> SiteConfigPublic:
    cfg = get_or_create_site_config(db)
    for k, v in payload.model_dump(exclude_unset=True).items():
        if v is not None:
            setattr(cfg, k, v)
    db.commit()
    db.refresh(cfg)
    return cfg


# Favicon upload: stored inline as a base64 data URI in favicon_url so it needs no
# separate file store / serve route and works across containers via the DB.
_FAVICON_TYPES = {
    "image/png",
    "image/x-icon",
    "image/vnd.microsoft.icon",
    "image/svg+xml",
    "image/jpeg",
    "image/gif",
    "image/webp",
}
_FAVICON_MAX_BYTES = 512 * 1024  # 512 KB — plenty for any favicon


@router.post("/site-config/favicon", response_model=SiteConfigPublic)
async def upload_favicon(
    file: UploadFile = File(...), db: Session = Depends(get_db)
) -> SiteConfigPublic:
    # Bound memory: reject by declared size first, then read at most the cap+1 so a
    # huge upload can't be buffered into RAM before the length check.
    if file.size is not None and file.size > _FAVICON_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Favicon too large (max 512 KB).")
    data = await file.read(_FAVICON_MAX_BYTES + 1)
    if not data:
        raise HTTPException(status_code=400, detail="Empty file.")
    if len(data) > _FAVICON_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Favicon too large (max 512 KB).")
    mime = (file.content_type or "").split(";")[0].strip().lower()
    if mime not in _FAVICON_TYPES:
        raise HTTPException(
            status_code=415,
            detail="Unsupported image type. Use PNG, ICO, SVG, JPEG, GIF, or WEBP.",
        )
    cfg = get_or_create_site_config(db)
    cfg.favicon_url = f"data:{mime};base64,{base64.b64encode(data).decode()}"
    db.commit()
    db.refresh(cfg)
    return cfg


# ---- Background workers (admin-controlled concurrency, applied live) --------
class WorkersIn(BaseModel):
    worker_concurrency: int  # 0 = use the WORKER_CONCURRENCY env default


def _live_worker_count() -> int:
    """Live RQ workers across all worker containers (via Redis). -1 = unknown.

    Worker.all() keeps returning a worker that died ungracefully until its Redis key
    expires (~worker_heartbeat_ttl+60s), so it transiently over-counts after a redeploy.
    Filter to workers whose last heartbeat is recent: a healthy idle worker re-registers
    ~every (ttl-15)s and a busy one every 30s, so a 1.5x-ttl window passes every live
    worker with margin while dropping the dead ones well before their key expires.
    """
    if not settings.redis_url:
        return -1
    try:
        import redis
        from rq import Worker

        conn = redis.Redis.from_url(settings.redis_url, socket_connect_timeout=2)
        fresh_within = settings.worker_heartbeat_ttl * 1.5
        now = datetime.now(timezone.utc)
        return sum(
            1
            for w in Worker.all(connection=conn)
            if w.last_heartbeat is not None
            and (now - w.last_heartbeat).total_seconds() <= fresh_within
        )
    except Exception:  # noqa: BLE001 — Redis unreachable / thread-mode deploy
        return -1


def _workers_state(db: Session) -> dict:
    cfg = get_or_create_site_config(db)
    stored = cfg.worker_concurrency or 0
    return {
        "stored": stored,                     # the admin override (0 = unset)
        "env_default": settings.worker_concurrency,
        "target": stored if stored > 0 else settings.worker_concurrency,  # per container
        "live": _live_worker_count(),         # total processes across all worker containers
    }


@router.get("/workers")
def get_workers(db: Session = Depends(get_db)) -> dict:
    return _workers_state(db)


@router.put("/workers")
def set_workers(body: WorkersIn, db: Session = Depends(get_db)) -> dict:
    if not (0 <= body.worker_concurrency <= 64):
        raise HTTPException(status_code=400, detail="worker_concurrency must be 0..64 (0 = env default).")
    cfg = get_or_create_site_config(db)
    cfg.worker_concurrency = body.worker_concurrency
    db.commit()
    return _workers_state(db)
