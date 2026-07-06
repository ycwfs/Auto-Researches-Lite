"""RQ worker entrypoint (used by the `worker` container).

Run with: python -m app.workers.run_worker — requires REDIS_URL.

A supervisor process keeps a pool of RQ worker subprocesses sized to the admin-controlled
target (SiteConfig.worker_concurrency, falling back to the WORKER_CONCURRENCY env). It
polls that target and converges the pool WITHOUT a restart: growing spawns more workers
instantly; shrinking sends a graceful stop (RQ finishes the in-flight job, then exits) to
the extras. Crashed workers are respawned (self-healing). Run more `worker` containers to
multiply capacity further (each runs its own supervisor → total = target × replicas).
"""
from __future__ import annotations

import logging
import os
import signal
import time

from app.core.config import settings
from app.core.database import init_db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("far.worker")

_POLL_SECONDS = 12
_stop = False


def _serve() -> None:
    """One RQ worker process. After a fork it must not reuse the parent's pooled
    DB / Redis connections, so dispose the engine and open a fresh Redis client."""
    import redis
    from rq import Queue, Worker

    from app.core.database import engine

    engine.dispose()  # drop connections inherited across the fork
    conn = redis.Redis.from_url(settings.redis_url)
    queue = Queue(settings.job_queue_name, connection=conn)
    # Short worker_ttl: an idle worker re-registers ~every (ttl-15)s and a worker killed
    # ungracefully expires from Redis ~(ttl+60)s later, so the admin "live" count (which
    # filters Worker.all() by heartbeat freshness) stays accurate. Busy workers heartbeat
    # on job_monitoring_interval regardless, so this never cuts a running job short.
    worker = Worker(  # RQ names it by host+pid (unique)
        [queue], connection=conn, worker_ttl=settings.worker_heartbeat_ttl
    )
    logger.info("RQ worker pid=%d started on queue '%s'", os.getpid(), settings.job_queue_name)
    worker.work(with_scheduler=True)  # RQ installs SIGINT/SIGTERM = warm shutdown


def _target_concurrency() -> int:
    """Admin target from SiteConfig (>0), else the WORKER_CONCURRENCY env default."""
    from app.core.database import SessionLocal
    from app.models.admin import SiteConfig

    val = 0
    db = SessionLocal()
    try:
        cfg = db.query(SiteConfig).first()
        val = (cfg.worker_concurrency or 0) if cfg else 0
    except Exception as exc:  # noqa: BLE001 — never let a transient DB blip kill the supervisor
        logger.warning("worker target lookup failed (%s); using env default", exc)
    finally:
        db.close()
    return max(1, val if val and val > 0 else settings.worker_concurrency)


def _spawn():
    from multiprocessing import Process

    from app.core.database import engine

    engine.dispose()  # fork-safe: no pooled connections inherited by the child
    p = Process(target=_serve, daemon=False)
    p.start()
    return p


def _reconcile_once() -> None:
    """Recover jobs stranded by a prior restart ONCE, before any worker drains."""
    import redis
    from rq import Queue

    from app.core.database import SessionLocal, engine
    from app.workers.reconcile import reap_stale_jobs, reconcile_orphaned_jobs

    conn = redis.Redis.from_url(settings.redis_url)
    db = SessionLocal()
    queue = Queue(settings.job_queue_name, connection=conn)
    try:
        reconcile_orphaned_jobs(db, rq_queue=queue)
        reap_stale_jobs(db, rq_queue=queue)
    finally:
        db.close()
        engine.dispose()


def main() -> None:
    if not settings.redis_url:
        raise SystemExit("REDIS_URL is required to run the RQ worker.")
    init_db()
    _reconcile_once()

    def _on_signal(_signum, _frame):
        global _stop
        _stop = True

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    workers: list = []   # actively managed worker processes
    retiring: list = []  # gracefully stopping (draining their last job), will exit
    last_target = None
    logger.info("worker supervisor starting (poll %ds)", _POLL_SECONDS)

    try:
        while not _stop:
            # is_alive() reaps finished children, so this prunes crashes AND collects
            # retired workers that have exited.
            workers = [p for p in workers if p.is_alive()]
            retiring = [p for p in retiring if p.is_alive()]

            target = _target_concurrency()
            if target != last_target:
                logger.info("worker target = %d (live %d)", target, len(workers))
                last_target = target

            if len(workers) < target:  # grow + self-heal crashed workers
                for _ in range(target - len(workers)):
                    workers.append(_spawn())
            elif len(workers) > target:  # retire the excess gracefully
                for p in workers[target:]:
                    try:
                        os.kill(p.pid, signal.SIGINT)  # RQ warm shutdown
                        retiring.append(p)
                    except ProcessLookupError:
                        pass
                workers = workers[:target]

            for _ in range(_POLL_SECONDS):  # stay responsive to shutdown
                if _stop:
                    break
                time.sleep(1)
    finally:
        logger.info("supervisor stopping; signaling %d worker(s)", len(workers) + len(retiring))
        for p in workers + retiring:
            try:
                os.kill(p.pid, signal.SIGINT)
            except (ProcessLookupError, Exception):  # noqa: BLE001
                pass
        for p in workers + retiring:
            try:
                p.join(timeout=30)
            except Exception:  # noqa: BLE001
                pass


if __name__ == "__main__":
    main()
