"""Re-summarize task entrypoint (force re-run of a paper's Summary / code analysis)."""
from __future__ import annotations

from app.core.database import SessionLocal
from app.services.paper_ingest_service import run_resummarize


def run(job_id: int) -> None:
    db = SessionLocal()
    try:
        run_resummarize(db, job_id)
    finally:
        db.close()
