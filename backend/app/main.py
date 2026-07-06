"""FastAPI application entrypoint for Semi-Auto Research."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.database import init_db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("far")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    from app.core.database import SessionLocal
    from app.services.seed import seed_defaults

    db = SessionLocal()
    try:
        seed_defaults(db)
        # Job recovery is best-effort: a transient Redis/RQ hiccup here must never stop
        # the API from starting.
        try:
            from app.workers.reconcile import reap_stale_jobs, reconcile_orphaned_jobs

            if settings.job_sync:
                pass  # sync mode: jobs run inline, never orphaned
            elif settings.redis_url:
                # Reap jobs a dead worker left "running" forever, so the UI doesn't show
                # a dead job spinning. Gated on RQ liveness (StartedJobRegistry) so a
                # slow-but-live worker job is never touched; the worker re-enqueues
                # recent orphans on its own startup.
                from app.workers.queue import _redis

                conn = _redis()
                if conn is not None:
                    from rq import Queue

                    reap_stale_jobs(db, rq_queue=Queue(settings.job_queue_name, connection=conn))
            else:
                # No Redis: in-process jobs die with this process — re-enqueue them.
                reconcile_orphaned_jobs(db)
        except Exception:  # noqa: BLE001 — startup must not hinge on job recovery
            logger.warning("startup job recovery skipped", exc_info=True)
    finally:
        db.close()
    logger.info(
        "Semi-Auto Research API started (offline_mode=%s, db=%s)",
        settings.is_offline,
        settings.database_url.split("://", 1)[0],
    )
    yield


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {
        "status": "ok",
        "app": settings.app_name,
        "offline_mode": settings.is_offline,
        "environment": settings.environment,
    }


def _mount_routers() -> None:
    from app.api.routes import (
        admin,
        auth,
        chat,
        context,
        credentials,
        discovery,
        jobs,
        models,
        projects,
        site,
        sources,
        zotero,
    )

    for module in (
        auth,
        projects,
        credentials,
        discovery,
        jobs,
        zotero,
        context,
        chat,
        models,
        admin,
        sources,
        site,
    ):
        app.include_router(module.router, prefix=settings.api_prefix)


_mount_routers()
