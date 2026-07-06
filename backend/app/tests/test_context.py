"""Project context: background refresh, steering context, and prompt steering."""
from __future__ import annotations

from fastapi.testclient import TestClient


def test_background_refreshes_on_project_update(auth_client: TestClient) -> None:
    pid = auth_client.post(
        "/api/projects", json={"name": "Orig Name", "keywords": ["alpha"]}
    ).json()["id"]
    ctx0 = auth_client.get(f"/api/projects/{pid}/context").json()
    assert "Orig Name" in ctx0["background"]

    auth_client.patch(f"/api/projects/{pid}", json={"name": "New Name", "keywords": ["beta", "gamma"]})
    ctx1 = auth_client.get(f"/api/projects/{pid}/context").json()
    assert "New Name" in ctx1["background"]
    assert "beta, gamma" in ctx1["background"]  # keywords are re-rendered


def test_build_steering_context_is_compact_and_focused(auth_client: TestClient) -> None:
    from app.core.database import SessionLocal
    from app.models.project import Project
    from app.services import context_service

    pid = auth_client.post(
        "/api/projects", json={"name": "Steer Test", "keywords": ["sparse-attention"]}
    ).json()["id"]
    db = SessionLocal()
    try:
        proj = db.get(Project, pid)
        ctx = context_service.get_or_create(db, proj)
        ctx.background = "B" * 5000  # an over-long background must not starve the rest
        ctx.references = "X" * 5000  # bulky — must NOT bloat the steering context
        db.commit()

        steer = context_service.build_steering_context(db, proj)
        assert "XXXX" not in steer  # references excluded
        assert steer.count("B") <= 600  # background contribution is capped
        assert len(steer) <= 1400
    finally:
        db.close()


def test_summarize_paper_prompt_includes_context(monkeypatch) -> None:
    from app.services.llm import LLMConfig, LLMService

    svc = LLMService(LLMConfig(provider="claude", api_key="x"))  # offline=False
    cap: dict = {}

    def rec(prompt, **_kw):
        cap["p"] = prompt
        return '{"summary_en":"a","summary_zh":"b","relevance":0.5}'

    monkeypatch.setattr(svc, "_complete", rec)
    svc.summarize_paper({"title": "T", "abstract": "A"}, ["kw"], context="STEER-MARKER-123")
    assert "STEER-MARKER-123" in cap["p"]
