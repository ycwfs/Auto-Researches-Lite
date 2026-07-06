"""Discovery task entrypoint — opens its own DB session."""
from __future__ import annotations

from app.core.database import SessionLocal
from app.services.discovery_service import run_discovery


def run(job_id: int) -> None:
    db = SessionLocal()
    try:
        run_discovery(db, job_id)
    finally:
        db.close()
