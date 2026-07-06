"""Zotero upload task entrypoint (async sync of papers + notes + PDF links)."""
from __future__ import annotations

from app.core.database import SessionLocal
from app.services.zotero_service import run_upload_job


def run(job_id: int) -> None:
    db = SessionLocal()
    try:
        run_upload_job(db, job_id)
    finally:
        db.close()
