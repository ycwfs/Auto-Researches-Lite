"""Job submission abstraction.

Uses Redis + RQ when REDIS_URL is set and reachable; otherwise runs the task
in a daemon thread in-process. Either way the task entrypoint receives only a
job_id and opens its own DB session, so the two paths are interchangeable.
"""
from __future__ import annotations

import importlib
import logging
import threading

from app.core.config import settings

logger = logging.getLogger("far.queue")

_redis_conn = None
_redis_checked = False


def _redis():
    global _redis_conn, _redis_checked
    if _redis_checked:
        return _redis_conn
    _redis_checked = True
    if not settings.redis_url:
        return None
    try:
        import redis

        conn = redis.Redis.from_url(settings.redis_url, socket_connect_timeout=2)
        conn.ping()
        _redis_conn = conn
        logger.info("Job queue: using Redis/RQ at %s", settings.redis_url)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Redis unavailable (%s); running jobs in-process", exc)
        _redis_conn = None
    return _redis_conn


def _run_in_thread(dotted_path: str, job_id: int) -> None:
    module_name, func_name = dotted_path.rsplit(".", 1)
    func = getattr(importlib.import_module(module_name), func_name)

    def _target() -> None:
        try:
            func(job_id)
        except Exception:  # noqa: BLE001
            logger.exception("in-process job %s (%s) crashed", job_id, dotted_path)

    threading.Thread(target=_target, name=f"job-{job_id}", daemon=True).start()


def submit(dotted_path: str, job_id: int) -> None:
    """Enqueue a task entrypoint `(job_id) -> None` for background execution."""
    if settings.job_sync:
        run_sync(dotted_path, job_id)
        return
    conn = _redis()
    if conn is not None:
        from rq import Queue

        queue = Queue(settings.job_queue_name, connection=conn)
        queue.enqueue(dotted_path, job_id, job_timeout=3600)
    else:
        _run_in_thread(dotted_path, job_id)


def run_sync(dotted_path: str, job_id: int) -> None:
    """Run a task entrypoint synchronously (used in tests)."""
    module_name, func_name = dotted_path.rsplit(".", 1)
    func = getattr(importlib.import_module(module_name), func_name)
    func(job_id)


def find_inflight(db, project_id: int, job_type, within_minutes: int = 30):
    """A queued/running job of `job_type` for the project (within a recency window so
    a dead/stuck job doesn't block forever), else None.

    Used to refuse a duplicate concurrent run — e.g. a double-clicked or manual+scheduled
    discovery — that would otherwise race the per-project dedup and store every paper twice.
    """
    from datetime import datetime, timedelta, timezone

    from app.models.enums import JobStatus
    from app.models.job import Job

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=within_minutes)
    for j in (
        db.query(Job)
        .filter(
            Job.project_id == project_id,
            Job.type == job_type,
            Job.status.in_([JobStatus.queued, JobStatus.running]),
        )
        .order_by(Job.id.desc())
        .all()
    ):
        ts = j.updated_at
        if ts is not None and ts.tzinfo is None:  # SQLite stores naive UTC
            ts = ts.replace(tzinfo=timezone.utc)
        if ts is None or ts >= cutoff:
            return j
    return None
