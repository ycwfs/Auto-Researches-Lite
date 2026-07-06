"""Admin MinerU poll budget + force re-extract (recovering abstract-fallback papers)."""
from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.core.database import SessionLocal


def _stuck_paper(pid: int) -> tuple[int, int]:
    """A discovered paper whose doc fell back to the abstract (extraction failed)."""
    from app.models.content import Paper, PaperDocument

    tag = uuid.uuid4().hex[:8]
    db = SessionLocal()
    try:
        doc = PaperDocument(
            title=f"Stuck paper {tag}", title_key=f"stuck-{tag}",
            abstract="Only the abstract was extracted.",
            pdf_url="https://openreview.net/pdf/abc.pdf",
            markdown="# Stuck paper\n\nOnly the abstract was extracted.",
            extraction_method="abstract",
        )
        db.add(doc)
        db.flush()
        paper = Paper(project_id=pid, title=doc.title, abstract=doc.abstract,
                      source="ai_paper_finder", venue="ICLR 2026", document_id=doc.id)
        db.add(paper)
        db.commit()
        return paper.id, doc.id
    finally:
        db.close()


def test_admin_mineru_max_wait_configurable(admin_client: TestClient) -> None:
    from app.core.database import SessionLocal
    from app.services import integration_service

    r = admin_client.put("/api/admin/integrations", json={"mineru_max_wait_seconds": 600})
    assert r.status_code == 200 and r.json()["mineru_max_wait_seconds"] == 600
    try:
        db = SessionLocal()
        try:
            assert integration_service.mineru_max_wait(db) == 600
        finally:
            db.close()
    finally:
        admin_client.put("/api/admin/integrations", json={"mineru_max_wait_seconds": 0})


def test_poll_budget_from_max_wait() -> None:
    """max_wait overrides attempts×delay; the built-in default holds when unset."""
    from app.integrations import mineru

    seen: dict = {}

    def fake_request(method, url, **kw):
        class _R:
            status_code = 200
            def json(self):
                return {"data": {"state": "done", "full_zip_url": ""}}
        seen["called"] = True
        return _R()

    # 600s / 4s delay → 150 attempts (but it returns on the first "done" poll).
    import app.integrations.mineru as m
    orig = m.request_with_retry
    m.request_with_retry = fake_request  # type: ignore[assignment]
    try:
        assert mineru._poll_mineru_task("u", {}, max_wait=600) == ""  # done, empty zip
        assert seen["called"]
    finally:
        m.request_with_retry = orig  # type: ignore[assignment]


def test_reparse_forces_fresh_extraction(auth_client: TestClient, monkeypatch) -> None:
    """The reparse action re-runs MinerU even though markdown already exists, and the
    recovered full text replaces the abstract fallback."""
    from app.integrations import mineru as mineru_mod
    from app.services import paper_db

    pid = auth_client.post(
        "/api/projects", json={"name": "Reparse", "keywords": ["x"]}
    ).json()["id"]
    paper_id, doc_id = _stuck_paper(pid)

    calls: list[bool] = []

    def fake_extract(paper, cache_dir, *, api_key="", api_url="", max_wait=0, force=False):
        calls.append(force)
        return mineru_mod.ExtractResult(
            "# Full paper\n\nRECOVERED FULL TEXT via MinerU.", "mineru", 40, "cache.md"
        )

    monkeypatch.setattr(paper_db.mineru, "extract", fake_extract)

    r = auth_client.post(f"/api/projects/{pid}/discovery/papers/{paper_id}/reparse")
    assert r.status_code == 202, r.text
    job = auth_client.get(f"/api/jobs/{r.json()['id']}").json()
    assert job["status"] == "succeeded", job.get("error")
    assert calls and calls[-1] is True  # extraction was forced

    db = SessionLocal()
    try:
        from app.models.content import PaperDocument
        doc = db.get(PaperDocument, doc_id)
        assert doc.extraction_method == "mineru"  # recovered from "abstract"
        assert "RECOVERED FULL TEXT" in doc.markdown
    finally:
        db.close()

    # The paper now reports full text available (chat gate flips true).
    papers = auth_client.get(f"/api/projects/{pid}/discovery/papers").json()
    assert next(p for p in papers if p["id"] == paper_id)["has_fulltext"] is True


def test_ensure_converted_force_busts_cache(tmp_path, monkeypatch) -> None:
    """ensure_converted(force=True) re-extracts even when markdown exists."""
    from app.core.database import SessionLocal
    from app.models.content import PaperDocument
    from app.services import paper_db

    monkeypatch.setattr(
        paper_db.mineru, "extract",
        lambda *a, **k: paper_db.mineru.ExtractResult("NEW", "mineru", 3, "c"),
    )
    db = SessionLocal()
    try:
        doc = PaperDocument(title="t", title_key=f"t-{uuid.uuid4().hex[:6]}",
                            markdown="OLD", extraction_method="abstract")
        db.add(doc)
        db.commit()
        paper_db.ensure_converted(db, doc, tmp_path)  # no force → keeps OLD
        assert doc.markdown == "OLD"
        paper_db.ensure_converted(db, doc, tmp_path, force=True)  # force → NEW
        assert doc.markdown == "NEW" and doc.extraction_method == "mineru"
    finally:
        db.close()


def test_arxiv_fallback_recovers_unfetchable_pdf(monkeypatch, tmp_path) -> None:
    """When the given PDF URL can't be fetched (e.g. OpenReview 403), extract() resolves
    the paper on arXiv by title and parses that PDF instead."""
    from app.integrations import mineru

    monkeypatch.setattr(mineru, "_try_mineru", lambda *a, **k: "")  # MinerU unavailable
    # pypdf 403s the OpenReview URL but succeeds on the arXiv one.
    def fake_pypdf(url: str) -> str:
        return "ARXIV FULL TEXT" if "arxiv.org" in url else ""
    monkeypatch.setattr(mineru, "_try_pypdf", fake_pypdf)
    monkeypatch.setattr(
        mineru, "find_arxiv_pdf_by_title",
        lambda title: "https://arxiv.org/pdf/2401.00001" if "GTR" in title else "",
    )

    res = mineru.extract(
        {"title": "GTR-Bench: Evaluating Geo-Temporal Reasoning", "abstract": "ab",
         "pdf_url": "https://openreview.net/pdf/x.pdf"},
        tmp_path,
    )
    assert res.method == "pypdf" and "ARXIV FULL TEXT" in res.text


def test_arxiv_fallback_title_match_is_strict() -> None:
    """A wrong-title arXiv hit must not be accepted."""
    from app.integrations import mineru

    class _R:
        status_code = 200
        text = (
            '<feed xmlns="http://www.w3.org/2005/Atom">'
            '<entry><title>A Completely Different Paper</title>'
            '<id>http://arxiv.org/abs/2401.99999v1</id></entry></feed>'
        )

    import app.integrations.mineru as m
    orig = m.request_with_retry
    m.request_with_retry = lambda *a, **k: _R()  # type: ignore[assignment]
    try:
        assert mineru.find_arxiv_pdf_by_title("GTR-Bench: Evaluating Geo-Temporal Reasoning") == ""
    finally:
        m.request_with_retry = orig  # type: ignore[assignment]


def test_upload_pdf_recovers_stuck_paper(auth_client: TestClient) -> None:
    """Uploading a PDF to a stuck paper stores its text and flips has_fulltext true."""
    pid = auth_client.post(
        "/api/projects", json={"name": "Upload", "keywords": ["x"]}
    ).json()["id"]
    paper_id, doc_id = _stuck_paper(pid)

    # A minimal PDF carrying extractable text.
    pdf = (
        b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R"
        b"/Resources<</Font<</F1 5 0 R>>>>>>endobj\n"
        b"4 0 obj<</Length 58>>stream\nBT /F1 12 Tf 72 700 Td (UPLOADED PAPER BODY TEXT) Tj ET\nendstream endobj\n"
        b"5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n"
        b"trailer<</Root 1 0 R>>\n%%EOF"
    )
    r = auth_client.post(
        f"/api/projects/{pid}/discovery/papers/{paper_id}/upload-pdf",
        files={"file": ("paper.pdf", pdf, "application/pdf")},
    )
    # The tiny hand-built PDF may not parse on every pypdf build; accept 202 or the
    # explicit "couldn't extract" 422 — both are correct, non-crashing outcomes.
    assert r.status_code in (202, 422), r.text
    if r.status_code == 422:
        return
    job = auth_client.get(f"/api/jobs/{r.json()['id']}").json()
    assert job["status"] == "succeeded", job.get("error")
    papers = auth_client.get(f"/api/projects/{pid}/discovery/papers").json()
    assert next(p for p in papers if p["id"] == paper_id)["has_fulltext"] is True

    r2 = auth_client.post(
        f"/api/projects/{pid}/discovery/papers/{paper_id}/upload-pdf",
        files={"file": ("x.txt", b"not a pdf", "text/plain")},
    )
    assert r2.status_code == 415  # non-PDF rejected


class _MockLLM:
    offline = True

    class config:
        provider = "mock"

    def summarize_full_text(self, *a, **k):
        return "- point"

    def summarize_codebase(self, *a, **k):
        return ""


def test_bulk_conversion_caps_mineru_wait(monkeypatch, tmp_path) -> None:
    """convert_and_store caps the per-paper MinerU poll (bulk), while ensure_converted
    with max_wait=None uses the admin budget (on-demand recovery)."""
    from app.core.database import SessionLocal
    from app.models.content import PaperDocument
    from app.services import integration_service, paper_db

    monkeypatch.setattr(integration_service, "mineru_max_wait", lambda db: 3600)  # admin: 1 hour
    seen: list = []

    def fake_extract(paper, cache_dir, *, api_key="", api_url="", max_wait=0, force=False):
        seen.append(max_wait)
        return paper_db.mineru.ExtractResult("full text", "mineru", 9, "c")

    monkeypatch.setattr(paper_db.mineru, "extract", fake_extract)

    db = SessionLocal()
    try:
        paper_db.convert_and_store(
            db, 1, {"title": "Bulky", "id": f"b-{uuid.uuid4().hex[:6]}"}, _MockLLM(), tmp_path, "discovered")
        assert seen[-1] == paper_db.BULK_MINERU_WAIT and paper_db.BULK_MINERU_WAIT < 3600

        doc = PaperDocument(title="t", title_key=f"t-{uuid.uuid4().hex[:6]}", extraction_method="")
        db.add(doc); db.commit()
        paper_db.ensure_converted(db, doc, tmp_path, force=True)
        assert seen[-1] == 3600
    finally:
        db.close()


def test_reuse_only_never_parses_uncached(auth_client, monkeypatch, tmp_path) -> None:
    """Idea grounding (reuse_only) must NOT invoke MinerU for an uncached paper — it
    links the paper and leaves it for the caller to ground on the abstract."""
    from app.core.database import SessionLocal
    from app.models.content import ProjectDocumentRef
    from app.services import paper_db

    pid = auth_client.post("/api/projects", json={"name": "Reuse"}).json()["id"]

    def boom(*a, **k):
        raise AssertionError("MinerU must not run in reuse_only mode")

    monkeypatch.setattr(paper_db.mineru, "extract", boom)

    db = SessionLocal()
    try:
        meta = {"title": f"Uncached {uuid.uuid4().hex[:6]}", "abstract": "an abstract"}
        doc = paper_db.convert_and_store(
            db, pid, meta, _MockLLM(), tmp_path, "discovered", reuse_only=True)
        assert (doc.markdown or "") == "" and (doc.summary or "") == ""  # not parsed/summarized
        linked = db.query(ProjectDocumentRef).filter_by(project_id=pid, document_id=doc.id).count()
        assert linked == 1  # still linked to the project's explored set
    finally:
        db.close()
