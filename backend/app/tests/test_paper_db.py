"""Global paper database: identity dedup, lazy convert+summarize reuse, endpoint."""
from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi.testclient import TestClient


def test_get_or_create_dedups_by_arxiv_doi_title(auth_client: TestClient) -> None:
    from app.core.database import SessionLocal
    from app.services import paper_db

    db = SessionLocal()
    try:
        d1 = paper_db.get_or_create_document(db, {"arxiv_id": "2401.11111", "title": "Some Paper"})
        db.commit()
        # Same arxiv id with a version suffix → same doc (version stripped).
        d2 = paper_db.get_or_create_document(db, {"arxiv_id": "2401.11111v2", "title": "Other"})
        assert d2.id == d1.id

        d3 = paper_db.get_or_create_document(db, {"doi": "10.5/abc", "title": "Doi Paper"})
        db.commit()
        d4 = paper_db.get_or_create_document(db, {"doi": "10.5/abc", "title": "Doi Paper Reformatted"})
        assert d4.id == d3.id  # doi dedup

        d5 = paper_db.get_or_create_document(db, {"title": "A Neat Title!"})
        db.commit()
        d6 = paper_db.get_or_create_document(db, {"title": "a   neat title"})
        assert d6.id == d5.id  # normalized-title dedup
    finally:
        db.close()


def test_ensure_summarized_converts_once_then_reuses(auth_client: TestClient, monkeypatch) -> None:
    from app.core.database import SessionLocal
    from app.integrations import mineru
    from app.integrations.mineru import ExtractResult
    from app.services import paper_db
    from app.services.llm import LLMConfig, LLMService

    calls = {"extract": 0}

    def fake_extract(src, cache_dir, **_kw):
        calls["extract"] += 1
        return ExtractResult(text="# T\n\nbody text", method="mineru", chars=11, cache_file="/tmp/x")

    monkeypatch.setattr(mineru, "extract", fake_extract)
    llm = LLMService(LLMConfig(provider="mock"))  # offline → deterministic 5-point mock
    db = SessionLocal()
    try:
        doc = paper_db.get_or_create_document(db, {"arxiv_id": "2402.22222", "title": "Reuse Paper"})
        db.commit()
        paper_db.ensure_summarized(db, doc, llm, Path(tempfile.mkdtemp()))
        assert doc.markdown and doc.extraction_method == "mineru"
        assert doc.summary.startswith("1.")  # 5-point structure
        assert calls["extract"] == 1
        # Second call reuses the stored markdown+summary — no re-extraction (dedup).
        paper_db.ensure_summarized(db, doc, llm, Path(tempfile.mkdtemp()))
        assert calls["extract"] == 1
    finally:
        db.close()


def test_summarize_full_text_uses_exact_five_point_prompt(monkeypatch) -> None:
    from app.services.llm import LLMConfig, LLMService

    svc = LLMService(LLMConfig(provider="claude", api_key="x"))  # offline=False
    cap: dict = {}

    def rec(prompt, **_kw):
        cap["p"] = prompt
        return "1. a\n2. b\n3. c\n4. d\n5. e"

    monkeypatch.setattr(svc, "_complete", rec)
    svc.summarize_full_text("THE-PAPER-BODY")
    assert "Task definition" in cap["p"]
    assert "Evaluation indicators" in cap["p"]
    assert "Experimental results and conclusions" in cap["p"]
    assert "THE-PAPER-BODY" in cap["p"]


def test_projectout_coerces_null_idea_summary_limit() -> None:
    # Regression: on existing deployments the additive migration adds idea_summary_limit
    # as NULLABLE, so legacy rows are NULL. ProjectOut must coerce that (not 500), else
    # GET /projects fails and the dashboard is stuck "Loading…".
    from datetime import datetime, timezone
    from types import SimpleNamespace

    from app.schemas.project import ProjectOut

    legacy = SimpleNamespace(
        id=1, name="x", description="", categories=[], keywords=[], max_results=20,
        max_total_papers=600, target_venue="neurips", paper_sources=["arxiv"],
        s2_recency_days=365, s2_fields_of_study="", s2_min_citations=0,
        paper_finder_venues=[], source_max_results={}, step_models={},
        min_papers_for_ideas=20, idea_summary_limit=None, discovery_schedule={},
        ideas_schedule={}, adr_project_dir="", stage="discovery",
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    )
    out = ProjectOut.model_validate(legacy)
    assert out.idea_summary_limit == 40  # coerced from NULL


def test_discovery_builds_paper_docs_and_endpoint_returns_5pt(
    auth_client: TestClient, monkeypatch
) -> None:
    from app.core.database import SessionLocal
    from app.integrations import mineru
    from app.integrations.mineru import ExtractResult
    from app.models.content import Paper
    from app.models.enums import JobType
    from app.models.job import Job
    from app.models.project import Project
    from app.services import discovery_service
    from app.services.llm import LLMConfig, LLMService

    monkeypatch.setattr(
        mineru, "extract",
        lambda src, cache_dir, **_kw: ExtractResult(
            text="# body\n\nmethod and results", method="mineru", chars=24, cache_file="/tmp/x"
        ),
    )

    pid = auth_client.post("/api/projects", json={"name": "Disc5pt", "keywords": ["x"]}).json()["id"]
    uid = auth_client.get("/api/auth/me").json()["id"]
    db = SessionLocal()
    try:
        proj = db.get(Project, pid)
        pa = Paper(project_id=pid, arxiv_id="2407.00001", title="Disc Paper A", abstract="a",
                   pdf_url="http://a", relevance=0.9, summary_en="brief a")
        pb = Paper(project_id=pid, arxiv_id="2407.00002", title="Disc Paper B", abstract="b",
                   pdf_url="http://b", relevance=0.5, summary_en="brief b")
        db.add(pa)
        db.add(pb)
        db.flush()
        job = Job(project_id=pid, user_id=uid, type=JobType.discovery)
        db.add(job)
        db.flush()
        discovery_service._build_paper_documents(db, job, proj, LLMService(LLMConfig(provider="mock")), [pa, pb])
        db.refresh(pa)
        assert pa.document_id and pb.document_id  # both linked to a PaperDocument
    finally:
        db.close()

    by_title = {p["title"]: p for p in auth_client.get(f"/api/projects/{pid}/discovery/papers").json()}
    assert by_title["Disc Paper A"]["document_id"]  # list carries the link (not the summary body)
    # The 5-point summary is lazy-loaded per paper.
    s = auth_client.get(f"/api/projects/{pid}/discovery/papers/{by_title['Disc Paper A']['id']}/summary")
    assert s.status_code == 200 and s.json()["summary_5pt"].startswith("1.")


def test_explored_papers_endpoint(auth_client: TestClient) -> None:
    from app.core.database import SessionLocal
    from app.services import paper_db

    pid = auth_client.post("/api/projects", json={"name": "PD", "keywords": ["x"]}).json()["id"]
    db = SessionLocal()
    try:
        doc = paper_db.get_or_create_document(
            db, {"arxiv_id": "2403.33333", "title": "Explored Paper", "abstract": "a", "source": "zotero"}
        )
        db.flush()
        doc.markdown = "# x\n\nbody"
        doc.summary = "1. task ..."
        doc.extraction_method = "mineru"
        db.commit()
        paper_db.link_project(db, pid, doc, "zotero")
    finally:
        db.close()

    docs = auth_client.get(f"/api/projects/{pid}/papers").json()
    match = [d for d in docs if d["title"] == "Explored Paper"]
    assert match, "explored paper not returned"
    assert match[0]["summary"].startswith("1.") and match[0]["has_markdown"] is True
    assert "markdown" not in match[0]  # full text is not shipped in the list payload
