"""Stale-job reaper, the cancel endpoint, and cooperative cancel in discovery."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient


def _seed_job(user_id: int, project_id: int, **kw):
    """Insert a Job row directly (backdated timestamps survive INSERT — onupdate only
    fires on UPDATE), returning its id."""
    from app.core.database import SessionLocal
    from app.models.enums import JobStatus, JobType
    from app.models.job import Job

    db = SessionLocal()
    try:
        job = Job(
            project_id=project_id,
            user_id=user_id,
            type=kw.pop("type", JobType.discovery),
            status=kw.pop("status", JobStatus.running),
            **kw,
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        return job.id
    finally:
        db.close()


def _owner_id(project_id: int) -> int:
    from app.core.database import SessionLocal
    from app.models.project import Project

    db = SessionLocal()
    try:
        return db.get(Project, project_id).owner_id
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# Reaper
# --------------------------------------------------------------------------- #
def test_reaper_marks_stale_jobs_terminal(auth_client: TestClient) -> None:
    from app.core.database import SessionLocal
    from app.models.enums import JobStatus
    from app.models.job import Job
    from app.workers.reconcile import reap_stale_jobs

    pid = auth_client.post("/api/projects", json={"name": "Reap"}).json()["id"]
    owner = _owner_id(pid)
    old = datetime.now(timezone.utc) - timedelta(minutes=45)
    now = datetime.now(timezone.utc)

    stale = _seed_job(owner, pid, status=JobStatus.running, created_at=old, updated_at=old)
    fresh = _seed_job(owner, pid, status=JobStatus.running, created_at=now, updated_at=now)
    stale_canceled = _seed_job(
        owner, pid, status=JobStatus.running, cancel_requested=True, created_at=old, updated_at=old
    )

    n = reap_stale_jobs(SessionLocal(), stale_after=timedelta(minutes=30))
    assert n >= 2

    db = SessionLocal()
    try:
        assert db.get(Job, stale).status == JobStatus.failed
        assert "interrupted" in (db.get(Job, stale).error or "").lower()
        assert db.get(Job, fresh).status == JobStatus.running  # fresh job untouched
        assert db.get(Job, stale_canceled).status == JobStatus.canceled  # cancel-requested -> canceled
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# Cancel endpoint
# --------------------------------------------------------------------------- #
def test_cancel_running_job_owner(auth_client: TestClient) -> None:
    from app.models.enums import JobStatus

    pid = auth_client.post("/api/projects", json={"name": "Cancel"}).json()["id"]
    jid = _seed_job(_owner_id(pid), pid, status=JobStatus.running)
    r = auth_client.post(f"/api/jobs/{jid}/cancel")
    assert r.status_code == 200, r.text
    assert r.json()["cancel_requested"] is True


def test_cancel_queued_job_stops_immediately(auth_client: TestClient) -> None:
    from app.models.enums import JobStatus

    pid = auth_client.post("/api/projects", json={"name": "CancelQ"}).json()["id"]
    jid = _seed_job(_owner_id(pid), pid, status=JobStatus.queued)
    r = auth_client.post(f"/api/jobs/{jid}/cancel")
    assert r.status_code == 200
    assert r.json()["status"] == "canceled"


def test_cancel_terminal_job_conflicts(auth_client: TestClient) -> None:
    from app.models.enums import JobStatus

    pid = auth_client.post("/api/projects", json={"name": "CancelDone"}).json()["id"]
    jid = _seed_job(_owner_id(pid), pid, status=JobStatus.succeeded)
    r = auth_client.post(f"/api/jobs/{jid}/cancel")
    assert r.status_code == 409


# --------------------------------------------------------------------------- #
# Cooperative cancel actually stops a running discovery job
# --------------------------------------------------------------------------- #
def test_discovery_honors_cancel(auth_client: TestClient, monkeypatch) -> None:
    from app.core.database import SessionLocal
    from app.models.content import Paper
    from app.models.enums import JobStatus, JobType
    from app.models.job import Job
    from app.services import discovery_service

    fake = [{
        "id": "2401.99999", "title": "Fake", "authors": [], "abstract": "x",
        "categories": [], "pdf_url": "", "published": "", "source": "arxiv",
    }]
    monkeypatch.setattr(
        "app.services.discovery_service.fetch_from_sources", lambda *a, **k: [dict(fake[0])]
    )
    pid = auth_client.post("/api/projects", json={"name": "CoopCancel"}).json()["id"]
    owner = _owner_id(pid)
    # Pre-set cancel_requested, then run the job directly: the first per-paper cancel
    # checkpoint must abort before any paper is stored.
    jid = _seed_job(owner, pid, status=JobStatus.running, cancel_requested=True, type=JobType.discovery)

    db = SessionLocal()
    try:
        discovery_service.run_discovery(db, jid)
        db.expire_all()
        assert db.get(Job, jid).status == JobStatus.canceled
        assert db.query(Paper).filter(Paper.project_id == pid).count() == 0
    finally:
        db.close()


def test_add_paper_honors_cancel(auth_client: TestClient, monkeypatch) -> None:
    from app.core.database import SessionLocal
    from app.models.content import Paper
    from app.models.enums import JobStatus, JobType
    from app.models.job import Job
    from app.services import paper_ingest_service

    monkeypatch.setattr(
        "app.integrations.auto_research.fetch_arxiv_paper",
        lambda _id: {"id": "2401.1", "title": "X", "authors": [], "abstract": "x",
                     "categories": [], "pdf_url": "", "published": "", "source": "arxiv"},
    )
    pid = auth_client.post("/api/projects", json={"name": "AddCancel"}).json()["id"]
    jid = _seed_job(
        _owner_id(pid), pid, status=JobStatus.running, cancel_requested=True,
        type=JobType.add_paper, payload={"kind": "arxiv", "value": "2401.1"},
    )
    db = SessionLocal()
    try:
        paper_ingest_service.run_add_paper(db, jid)
        db.expire_all()
        assert db.get(Job, jid).status == JobStatus.canceled
        assert db.query(Paper).filter(Paper.project_id == pid).count() == 0
    finally:
        db.close()


