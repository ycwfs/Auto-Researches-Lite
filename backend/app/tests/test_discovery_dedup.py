"""Discovery dedup: in-flight guard against concurrent runs + the dedup key."""
from __future__ import annotations

from fastapi.testclient import TestClient


def test_dedup_key_collapses_version_and_normalizes_title() -> None:
    from app.services.discovery_service import _dedup_key

    # arXiv id with a version suffix matches the same id without it.
    assert _dedup_key("2606.06338v2", "A") == _dedup_key("2606.06338", "B")
    # No arXiv id → normalized title (whitespace/case).
    assert _dedup_key("", "Deep  Nets") == _dedup_key(None, "deep nets")


def test_discovery_run_returns_inflight_job_instead_of_duplicate(auth_client: TestClient) -> None:
    from app.core.database import SessionLocal
    from app.models.enums import JobStatus, JobType
    from app.models.job import Job

    pid = auth_client.post("/api/projects", json={"name": "DupGuard", "keywords": ["x"]}).json()["id"]
    uid = auth_client.get("/api/auth/me").json()["id"]

    db = SessionLocal()
    try:
        j = Job(project_id=pid, user_id=uid, type=JobType.discovery, status=JobStatus.running)
        db.add(j)
        db.commit()
        jid = j.id
    finally:
        db.close()

    # A new run while one is in flight returns the existing job — no second run.
    r = auth_client.post(f"/api/projects/{pid}/discovery/run")
    assert r.status_code == 202 and r.json()["id"] == jid
