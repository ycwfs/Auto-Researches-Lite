"""Re-enqueue orphaned jobs so a restart can't strand them as `queued` forever.

A job is *orphaned* when its DB row is still `queued`/`running` but no matching
entry exists in the queue — e.g. Redis was restarted without persistence, or the
worker died mid-job. Without recovery these rows sit at progress 0 indefinitely
(the symptom: "discovery queued for a long time").

Called on **worker** startup (RQ path, with the live queue so still-enqueued jobs
are skipped) and, in the no-Redis **in-process** fallback, on backend startup.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.models.enums import JobStatus, JobType
from app.models.job import Job
from app.workers.queue import submit

logger = logging.getLogger("far.reconcile")

# Job type -> task entrypoint dotted path. Mirrors the route submit() call sites —
# EVERY enqueued JobType must be here or a worker restart strands its in-flight jobs
# as "running" forever (the reconciler can neither requeue nor fail an unmapped type).
_TASK_PATHS: dict[JobType, str] = {
    JobType.discovery: "app.workers.tasks_discovery.run",
    # The AI Paper Finder shares the discovery task (it branches on the job type).
    JobType.paper_finder: "app.workers.tasks_discovery.run",
    JobType.resummarize: "app.workers.tasks_resummarize.run",
    JobType.add_paper: "app.workers.tasks_paper.run",
    JobType.zotero_upload: "app.workers.tasks_zotero.run",
}

# Jobs touched more recently than this are assumed to be legitimately in-flight
# (a healthy worker flips queued -> running within seconds), so we leave them be.
_STALE_AFTER = timedelta(seconds=120)


def _live_db_job_ids(rq_queue) -> set[int]:
    """DB job_ids already represented in the RQ queue or its started registry."""
    from rq.job import Job as RQJob
    from rq.registry import StartedJobRegistry

    ids = list(rq_queue.get_job_ids())
    ids += StartedJobRegistry(queue=rq_queue).get_job_ids()
    live: set[int] = set()
    for rq_job in RQJob.fetch_many(ids, connection=rq_queue.connection):
        if rq_job is not None and rq_job.args:
            live.add(rq_job.args[0])
    return live


def _is_stale(job: Job, now: datetime, stale_after: timedelta) -> bool:
    ts = job.updated_at
    if ts.tzinfo is None:  # SQLite stores naive timestamps; treat as UTC.
        ts = ts.replace(tzinfo=timezone.utc)
    return (now - ts) >= stale_after


def reap_stale_jobs(db: Session, rq_queue=None, *, stale_after: Optional[timedelta] = None) -> int:
    """Mark long-stale `queued`/`running` jobs terminal so the UI never shows a job
    running forever after its worker was killed mid-flight. Returns the count reaped.

    Liveness is judged by RQ, not by `updated_at`: when `rq_queue` is given, any job RQ
    still has queued or started is skipped — so a slow-but-live worker job (whose Job
    row only commits every few items) is NEVER reaped. `updated_at` staleness
    (`stale_after`, default `settings.stale_job_minutes`) is only a secondary guard
    against reaping a just-submitted job. A cancel-requested job becomes `canceled`;
    otherwise `failed` (it was interrupted, not completed).
    """
    from app.core.config import settings

    if stale_after is None:
        stale_after = timedelta(minutes=settings.stale_job_minutes)
    mins = max(1, int(stale_after.total_seconds() // 60))
    now = datetime.now(timezone.utc)
    live = _live_db_job_ids(rq_queue) if rq_queue is not None else set()
    reaped = 0
    candidates = (
        db.query(Job)
        .filter(Job.status.in_([JobStatus.queued, JobStatus.running]))
        .order_by(Job.id)
        .all()
    )
    for job in candidates:
        if job.id in live or not _is_stale(job, now, stale_after):
            continue  # RQ still owns it (live), or too recent to judge dead
        if job.cancel_requested:
            job.status = JobStatus.canceled
            job.log = (job.log or "") + f"\n[reaper] canceled — no worker progress for over {mins} min."
        else:
            job.status = JobStatus.failed
            job.error = (
                f"Interrupted — no worker progress for over {mins} minutes "
                "(the worker was likely restarted). Re-run when ready."
            )
            job.log = (job.log or "") + f"\n[reaper] failed — stale for over {mins} min."
        db.commit()
        reaped += 1
        logger.info("[reaper] %s stale job %s (type=%s)", job.status.value, job.id, job.type.value)
    if reaped:
        logger.info("[reaper] reaped %d stale job(s)", reaped)
    return reaped


def reconcile_orphaned_jobs(
    db: Session, rq_queue=None, *, stale_after: timedelta = _STALE_AFTER
) -> int:
    """Re-enqueue stale `queued`/`running` jobs. Returns the count re-enqueued.

    When `rq_queue` is given (worker path), jobs still present in the live queue
    are skipped to avoid double-enqueueing. Without it (in-process fallback),
    every stale job is re-enqueued because no durable queue survived.
    """
    now = datetime.now(timezone.utc)
    candidates = (
        db.query(Job)
        .filter(Job.status.in_([JobStatus.queued, JobStatus.running]))
        .order_by(Job.id)
        .all()
    )
    live = _live_db_job_ids(rq_queue) if rq_queue is not None else set()
    requeued = 0
    for job in candidates:
        if not _is_stale(job, now, stale_after) or job.id in live:
            continue
        path: Optional[str] = _TASK_PATHS.get(job.type)
        if path is None:
            logger.warning("job %s: no task mapping for type=%s; skipping", job.id, job.type)
            continue
        job.status = JobStatus.queued
        job.progress = 0
        job.log = (job.log or "") + "\n[reconcile] re-enqueued after restart (was orphaned)."
        db.add(job)
        db.commit()  # commit before enqueue so the worker always sees the row
        submit(path, job.id)
        requeued += 1
        logger.info("re-enqueued orphaned job %s (type=%s)", job.id, job.type.value)
    logger.info("reconcile: re-enqueued %d orphaned job(s)", requeued)
    return requeued
