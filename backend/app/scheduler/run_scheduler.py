"""Poller that enqueues due discovery jobs from per-project schedules.

Runs as its own service (`python -m app.scheduler.run_scheduler`). Every tick it
checks each project's `discovery_schedule` and enqueues a job when due. The schedule
carries `time_utc` (HH:MM in `tz`) and an IANA `tz` (default UTC). Firing is keyed to
the scheduled "slot" (today's instant) so the daily fetch runs every day and re-fires
the same day if the user changes the time; `last_slot`/`last_run` record the last firing.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.database import SessionLocal, init_db
from app.models.enums import JobType
from app.models.job import Job
from app.models.project import Project
from app.models.user import User
from app.services.quota import can_run_discovery
from app.workers.queue import submit

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("far.scheduler")


def _hhmm(time_utc, default: tuple[int, int] = (8, 0)) -> tuple[int, int]:
    try:
        hh, mm = (int(x) for x in str(time_utc).split(":")[:2])
        return hh, mm
    except (ValueError, AttributeError, TypeError):
        return default


def _parse_last(last) -> datetime | None:
    if not last:
        return None
    try:
        dt = datetime.fromisoformat(last)
    except (ValueError, TypeError):
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _tz(tz_name):
    """Resolve a schedule's IANA tz name; unknown/empty falls back to UTC."""
    if not tz_name or tz_name == "UTC":
        return timezone.utc
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(str(tz_name))
    except Exception:  # noqa: BLE001
        return timezone.utc


def _target_utc(now: datetime, sched: dict) -> datetime:
    """Today's scheduled instant (HH:MM in the schedule's tz) as a UTC datetime."""
    local = now.astimezone(_tz(sched.get("tz", "UTC")))
    hh, mm = _hhmm(sched.get("time_utc", "08:00"))
    return local.replace(hour=hh, minute=mm, second=0, microsecond=0).astimezone(timezone.utc)


def slot_of(now: datetime, sched: dict) -> str:
    """Stable id of today's scheduled occurrence (used to fire each slot once)."""
    return _target_utc(now, sched).isoformat()


def _due_discovery(sched: dict, now: datetime) -> bool:
    """Fire when the scheduled time is reached and that exact slot hasn't fired.

    A 'slot' is today's scheduled instant; changing the time creates a new slot, so
    the daily fetch runs every day AND can run again the same day after a time change.
    """
    if not sched or not sched.get("enabled"):
        return False
    if now < _target_utc(now, sched):
        return False
    return sched.get("last_slot") != slot_of(now, sched)


def _enqueue(db: Session, project: Project, job_type: JobType, dotted: str) -> None:
    from app.workers.queue import find_inflight

    # Don't enqueue if a manual (or prior scheduled) run of this type is already in
    # flight — concurrent runs race the dedup and duplicate papers/ideas.
    if find_inflight(db, project.id, job_type) is not None:
        logger.info("skip %s for project %s — already in flight", job_type.value, project.id)
        return
    job = Job(project_id=project.id, user_id=project.owner_id, type=job_type)
    db.add(job)
    db.commit()
    db.refresh(job)
    submit(dotted, job.id)
    logger.info("enqueued %s job %s for project %s", job_type.value, job.id, project.id)


def tick(db: Session, now: datetime | None = None) -> int:
    """Enqueue all due jobs; returns the count enqueued. Pure + testable."""
    now = now or datetime.now(timezone.utc)
    count = 0
    for project in db.query(Project).all():
        owner = db.get(User, project.owner_id)
        if owner is None:
            continue
        if _due_discovery(project.discovery_schedule or {}, now):
            # Mark the slot fired regardless so we don't retry every tick. The quota
            # gate below is a no-op in the OSS edition (always allows).
            sched = dict(project.discovery_schedule or {})
            sched["last_run"] = now.isoformat()
            sched["last_slot"] = slot_of(now, sched)
            project.discovery_schedule = sched
            db.commit()
            if can_run_discovery(db, owner):
                _enqueue(db, project, JobType.discovery, "app.workers.tasks_discovery.run")
                count += 1
            else:
                logger.info(
                    "skip scheduled discovery for project %s — daily quota reached", project.id
                )
    return count


def _run_tick() -> None:
    db = SessionLocal()
    try:
        tick(db)
    except Exception:  # noqa: BLE001
        logger.exception("scheduler tick failed")
    finally:
        db.close()


def main() -> None:
    from apscheduler.schedulers.blocking import BlockingScheduler

    init_db()
    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(_run_tick, "interval", seconds=60, next_run_time=datetime.now(timezone.utc))
    logger.info("Semi-Auto Research scheduler started (60s tick).")
    scheduler.start()


if __name__ == "__main__":
    main()
