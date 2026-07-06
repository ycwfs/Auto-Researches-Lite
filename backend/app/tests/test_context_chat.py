"""Project context + dialogue panel (offline)."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.scheduler.run_scheduler import _due_discovery, slot_of


def test_context_and_chat(auth_client: TestClient) -> None:
    pid = auth_client.post("/api/projects", json={"name": "Ctx", "keywords": ["rl"]}).json()["id"]

    ctx = auth_client.get(f"/api/projects/{pid}/context").json()
    assert ctx["stage"] == "discovery"

    r = auth_client.post(f"/api/projects/{pid}/chat", json={"message": "What should I do next?"})
    assert r.status_code == 200
    assert r.json()["role"] == "assistant"
    assert r.json()["content"]

    history = auth_client.get(f"/api/projects/{pid}/chat").json()
    assert [m["role"] for m in history] == ["user", "assistant"]


def test_schedule_due_logic() -> None:
    now = datetime(2026, 5, 28, 9, 0, tzinfo=timezone.utc)
    assert _due_discovery({"enabled": True, "time_utc": "08:00"}, now) is True
    assert _due_discovery({"enabled": True, "time_utc": "10:00"}, now) is False
    assert _due_discovery({"enabled": False, "time_utc": "08:00"}, now) is False
    # Already fired this slot -> not due.
    sched = {"enabled": True, "time_utc": "08:00"}
    assert _due_discovery({**sched, "last_slot": slot_of(now, sched)}, now) is False
