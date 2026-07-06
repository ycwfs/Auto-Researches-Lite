"""Cooperative cancellation for background jobs.

The cancel endpoint (`POST /jobs/{id}/cancel`) sets `Job.cancel_requested`; a running
service notices it at a checkpoint and aborts. Two non-obvious constraints:

- `SessionLocal` uses ``expire_on_commit=False``, so after the service's own
  ``db.commit()`` the in-memory ``job`` keeps its originally-loaded
  ``cancel_requested`` value. A check MUST ``db.refresh(job)`` first or it never sees
  the route's concurrent write.
- ``JobCanceled`` inherits ``BaseException`` (like ``KeyboardInterrupt``) so the
  services' broad ``except Exception`` blocks — which route errors to ``_fail`` — do
  NOT swallow a cancel into a "failed" status.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.models.enums import JobStatus
from app.models.job import Job

logger = logging.getLogger("far.job_control")

_TERMINAL = (JobStatus.succeeded, JobStatus.failed, JobStatus.canceled)


class JobCanceled(BaseException):
    """Raised from a checkpoint to abort a running job. NOT an Exception, so a
    service's generic ``except Exception`` handler can't turn it into a failure."""


def raise_if_canceled(db: Session, job: Job) -> None:
    """Re-read the cancel flag and raise ``JobCanceled`` if the user requested a stop.

    The ``db.refresh`` is mandatory (expire_on_commit=False) — without it the loaded
    ``cancel_requested`` is stale and the cancel is never observed.
    """
    db.refresh(job)
    if job.cancel_requested:
        raise JobCanceled


def stop_requested(db: Session, job: Job) -> bool:
    """True if the job was already canceled or cancel-requested by the time the worker
    reached this point (e.g. a queued→canceled job that RQ still handed to a worker).
    Callers short-circuit so a canceled job is never resurrected to running."""
    db.refresh(job)
    return job.status in _TERMINAL or job.cancel_requested


def mark_canceled(db: Session, job: Job, line: str = "Canceled by user.") -> None:
    """Set the job to a canceled state and persist. Rolls back first so half-done work
    from the interrupted step is discarded, not committed. A persistence failure is
    swallowed (logged) — the reaper will finish the job off."""
    try:
        db.rollback()
        job.status = JobStatus.canceled
        job.error = ""
        job.log = (job.log or "") + line + "\n"
        db.commit()
    except Exception:  # noqa: BLE001 — leave it for the reaper rather than crash the worker
        logger.warning("mark_canceled failed for job %s; leaving for the reaper", job.id, exc_info=True)
