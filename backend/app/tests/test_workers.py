"""Admin-controlled background-worker concurrency: route + supervisor target."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient


def test_workers_get_set_and_range(admin_client: TestClient) -> None:
    s = admin_client.get("/api/admin/workers").json()
    assert {"stored", "env_default", "target", "live"} <= set(s)

    r = admin_client.put("/api/admin/workers", json={"worker_concurrency": 5})
    assert r.status_code == 200
    assert r.json()["stored"] == 5 and r.json()["target"] == 5

    # out of range rejected
    assert admin_client.put("/api/admin/workers", json={"worker_concurrency": 999}).status_code == 400
    assert admin_client.put("/api/admin/workers", json={"worker_concurrency": -1}).status_code == 400

    # 0 -> target falls back to the env default
    r0 = admin_client.put("/api/admin/workers", json={"worker_concurrency": 0}).json()
    assert r0["stored"] == 0 and r0["target"] == r0["env_default"]


def test_supervisor_target_reads_siteconfig(admin_client: TestClient) -> None:
    """The worker supervisor's _target_concurrency picks up the admin value from the DB."""
    from app.core.config import settings
    from app.workers.run_worker import _target_concurrency

    admin_client.put("/api/admin/workers", json={"worker_concurrency": 7})
    assert _target_concurrency() == 7

    admin_client.put("/api/admin/workers", json={"worker_concurrency": 0})
    assert _target_concurrency() == max(1, settings.worker_concurrency)


def test_live_worker_count_filters_stale_heartbeats(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only workers whose heartbeat is within 1.5x the TTL count as live; the ones
    that died ungracefully (stale heartbeat, still in Worker.all until their key expires)
    and any without a heartbeat are dropped."""
    import redis
    from rq import Worker

    from app.api.routes import admin as admin_mod
    from app.core.config import settings

    window = settings.worker_heartbeat_ttl * 1.5
    now = datetime.now(timezone.utc)

    class FakeWorker:
        def __init__(self, age_s: float | None) -> None:
            self.last_heartbeat = None if age_s is None else now - timedelta(seconds=age_s)

    workers = [
        FakeWorker(2),           # busy/just-heartbeated -> live
        FakeWorker(window - 1),  # at the edge, still fresh -> live
        FakeWorker(window + 30), # died ungracefully, key not yet expired -> dropped
        FakeWorker(None),        # no heartbeat recorded -> dropped
        FakeWorker(99_999),      # long-dead -> dropped
    ]

    monkeypatch.setattr(settings, "redis_url", "redis://unused", raising=False)
    monkeypatch.setattr(redis.Redis, "from_url", lambda *a, **k: object())
    monkeypatch.setattr(Worker, "all", lambda connection=None: workers)

    assert admin_mod._live_worker_count() == 2


def test_worker_heartbeat_ttl_floored() -> None:
    """A non-positive/too-small TTL is floored so the freshness window never collapses
    to <=0 (which would make the live count read 0 forever)."""
    from app.core.config import Settings

    assert Settings(worker_heartbeat_ttl=0).worker_heartbeat_ttl == 15
    assert Settings(worker_heartbeat_ttl=-5).worker_heartbeat_ttl == 15
    assert Settings(worker_heartbeat_ttl=90).worker_heartbeat_ttl == 90
