"""Orphaned-job reconciliation tests (workers/reconcile.py).

`submit` is monkeypatched to a recorder so no real task (or network) runs; we
only assert *which* jobs get re-enqueued.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.models.enums import JobStatus, JobType
from app.models.job import Job
from app.models.project import Project
from app.workers import reconcile as reconcile_mod


def _make_project(auth_client: TestClient) -> int:
    r = auth_client.post("/api/projects", json={"name": "Recon", "categories": ["cs.LG"]})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _insert_job(db, project: Project, *, age_seconds: int) -> int:
    ts = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    job = Job(
        project_id=project.id,
        user_id=project.owner_id,
        type=JobType.discovery,
        status=JobStatus.queued,
        created_at=ts,
        updated_at=ts,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job.id


def test_reconcile_reenqueues_stale_but_not_fresh(auth_client: TestClient, monkeypatch) -> None:
    pid = _make_project(auth_client)
    calls: list[tuple[str, int]] = []
    monkeypatch.setattr(reconcile_mod, "submit", lambda path, jid: calls.append((path, jid)))

    db = SessionLocal()
    try:
        project = db.get(Project, pid)
        stale_id = _insert_job(db, project, age_seconds=600)
        fresh_id = _insert_job(db, project, age_seconds=0)

        reconcile_mod.reconcile_orphaned_jobs(db)

        requeued_ids = [jid for _, jid in calls]
        assert ("app.workers.tasks_discovery.run", stale_id) in calls  # stale -> re-enqueued
        assert fresh_id not in requeued_ids  # fresh (in-flight) -> left alone

        db.expire_all()
        reloaded = db.get(Job, stale_id)
        assert reloaded.status == JobStatus.queued
        assert "[reconcile]" in reloaded.log
    finally:
        db.close()


def test_reconcile_covers_paper_finder_and_all_enqueued_types(
    auth_client: TestClient, monkeypatch
) -> None:
    """Regression: a worker restart mid AI-Paper-Finder run left the job 'running'
    forever because paper_finder had no task mapping. Every enqueued type must
    reconcile; the synchronous types are intentionally unmapped."""
    pid = _make_project(auth_client)
    calls: list[tuple[str, int]] = []
    monkeypatch.setattr(reconcile_mod, "submit", lambda path, jid: calls.append((path, jid)))

    db = SessionLocal()
    try:
        project = db.get(Project, pid)
        ts = datetime.now(timezone.utc) - timedelta(seconds=600)
        job = Job(
            project_id=project.id, user_id=project.owner_id,
            type=JobType.paper_finder, status=JobStatus.running,
            created_at=ts, updated_at=ts,
        )
        db.add(job)
        db.commit()
        db.refresh(job)

        reconcile_mod.reconcile_orphaned_jobs(db)
        assert (("app.workers.tasks_discovery.run", job.id)) in calls
        db.refresh(job)
        assert job.status == JobStatus.queued  # zombie 'running' row got requeued
    finally:
        db.close()

    # Map completeness: every enqueued JobType is mapped to a task entrypoint.
    assert set(reconcile_mod._TASK_PATHS) == set(JobType)
