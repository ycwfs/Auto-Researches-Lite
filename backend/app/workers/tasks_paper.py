"""Add-paper task entrypoint — opens its own DB session."""
from __future__ import annotations

from app.core.database import SessionLocal
from app.services.paper_ingest_service import run_add_paper


def run(job_id: int) -> None:
    db = SessionLocal()
    try:
        run_add_paper(db, job_id)
    finally:
        db.close()
