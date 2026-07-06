"""Self-healing of stale (offline-mock) bilingual paper summaries on discovery."""
from __future__ import annotations

from fastapi.testclient import TestClient

_MOCK = "[offline summary — configure an LLM key for full analysis]"


class _RealLLM:
    offline = False

    def summarize_paper(self, paper, keywords, context=""):
        return {"summary_en": f"REAL: {paper['title']}", "summary_zh": "真实摘要", "relevance": 0.8}


class _StillMockLLM:
    offline = False

    def summarize_paper(self, paper, keywords, context=""):
        return {"summary_en": "[offline summary — still down]", "summary_zh": "", "relevance": 0.1}


def test_heal_stale_bilingual_summary(auth_client: TestClient) -> None:
    from app.core.database import SessionLocal
    from app.models.content import Paper
    from app.models.project import Project
    from app.services import discovery_service as ds

    pid = auth_client.post("/api/projects", json={"name": "Heal"}).json()["id"]
    db = SessionLocal()
    try:
        proj = db.get(Project, pid)
        stale = Paper(project_id=pid, title="Stale", abstract="a", summary_en=_MOCK,
                      summary_zh="离线", relevance=0.1)
        fresh = Paper(project_id=pid, title="Fresh", abstract="a",
                      summary_en="A real summary.", summary_zh="真实", relevance=0.5)
        db.add_all([stale, fresh])
        db.commit()

        # a real LLM heals only the stale one
        assert ds._heal_stale_summaries(db, proj, _RealLLM(), "") == 1
        db.refresh(stale)
        db.refresh(fresh)
        assert stale.summary_en == "REAL: Stale" and stale.relevance == 0.8
        assert fresh.summary_en == "A real summary."  # untouched (not mock)

        # if the LLM is still returning a mock, do NOT overwrite the stale summary
        stale.summary_en = _MOCK
        db.commit()
        assert ds._heal_stale_summaries(db, proj, _StillMockLLM(), "") == 0
        db.refresh(stale)
        assert stale.summary_en == _MOCK  # left as-is, retried later
    finally:
        db.query(Paper).filter(Paper.project_id == pid).delete()
        db.commit()
        db.close()
