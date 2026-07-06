"""Zotero integration: graceful behavior without a configured key."""
from __future__ import annotations

import uuid

from fastapi.testclient import TestClient


def test_zotero_status_unconfigured(auth_client: TestClient) -> None:
    assert auth_client.get("/api/zotero/status").json() == {"configured": False}


def test_zotero_collections_requires_key(auth_client: TestClient) -> None:
    # No zotero credential set → clear 400 prompting the user to connect.
    r = auth_client.get("/api/zotero/collections")
    assert r.status_code == 400
    assert "not connected" in r.json()["detail"].lower()


def _connect_zotero(client: TestClient) -> None:
    client.put(
        "/api/credentials",
        json={"provider": "zotero",
              "data": {"api_key": "abc123", "library_id": "12345", "library_type": "user"}},
    )


def test_zotero_upload_uploads_only_selected_papers(auth_client: TestClient, monkeypatch) -> None:
    from app.core.database import SessionLocal
    from app.models.content import Paper
    from app.services import zotero_service

    _connect_zotero(auth_client)
    pid = auth_client.post("/api/projects", json={"name": "Z", "categories": ["cs.LG"]}).json()["id"]
    db = SessionLocal()
    try:
        rows = [Paper(project_id=pid, title="A"), Paper(project_id=pid, title="B"), Paper(project_id=pid, title="C")]
        db.add_all(rows)
        db.commit()
        ids = [r.id for r in rows]
    finally:
        db.close()

    captured: dict = {}
    monkeypatch.setattr(zotero_service, "pick_collection", lambda *a, **k: None)

    def _fake_upload(db, user, papers, project=None, papers_collection=None, progress=None):
        captured["titles"] = sorted(p.title for p in papers)
        return {"papers_uploaded": len(papers), "ideas_uploaded": 0,
                "notes_uploaded": 0, "attachments_uploaded": 0, "errors": []}

    monkeypatch.setattr(zotero_service, "upload_project", _fake_upload)

    # Async now: a Job is returned; JOB_SYNC runs it inline so the upload has finished.
    r = auth_client.post(
        "/api/zotero/upload",
        json={"project_id": pid, "paper_ids": [ids[0], ids[2]]},
    )
    assert r.status_code == 202, r.text
    job = auth_client.get(f"/api/jobs/{r.json()['id']}").json()
    assert job["status"] == "succeeded", job.get("error")
    assert captured["titles"] == ["A", "C"]  # only the two selected, not "B"
    assert "uploaded 2 papers" in job["log"]


def test_zotero_upload_requires_connection(auth_client: TestClient) -> None:
    pid = auth_client.post("/api/projects", json={"name": "ZX"}).json()["id"]
    r = auth_client.post("/api/zotero/upload", json={"project_id": pid, "paper_ids": [], "idea_ids": []})
    assert r.status_code == 400 and "not connected" in r.json()["detail"].lower()


def test_zotero_status_after_setting_key(auth_client: TestClient) -> None:
    auth_client.put(
        "/api/credentials",
        json={
            "provider": "zotero",
            "data": {"api_key": "abc123", "library_id": "12345", "library_type": "user"},
        },
    )
    assert auth_client.get("/api/zotero/status").json() == {"configured": True}



def test_zotero_upload_attaches_summary_code_and_pdf(auth_client, monkeypatch) -> None:
    """Each synced paper gets a Summary note, a code-analysis note, and a PDF link
    attached as child items (parentid = the paper's key)."""
    from app.core.database import SessionLocal
    from app.models.content import Paper, PaperDocument
    from app.services import zotero_service

    _connect_zotero(auth_client)
    pid = auth_client.post("/api/projects", json={"name": "ZAttach"}).json()["id"]
    db = SessionLocal()
    try:
        doc = PaperDocument(
            title="Efficient Attention", title_key="efficient-attention-zt",
            summary="- Contribution: memory-efficient **attention**.",
            code_status="ok", code_summary="- train.py runs the loop.",
            code_url="https://github.com/foo/bar", extraction_method="mineru", markdown="x",
        )
        db.add(doc); db.flush()
        paper = Paper(project_id=pid, title="Efficient Attention", abstract="ab",
                      pdf_url="https://openreview.net/pdf/x.pdf", authors=["A. B"],
                      document_id=doc.id)
        db.add(paper); db.commit()
        paper_id = paper.id
    finally:
        db.close()

    created: list[dict] = []

    class _FakeZot:
        def collections(self, limit=100):
            return []
        def create_collections(self, cols):
            return {"successful": {"0": {"key": "COLKEY"}}}
        def item_template(self, itemtype, linkmode=None):
            t = {"itemType": itemtype}
            if linkmode:
                t["linkMode"] = linkmode
            return t
        def create_items(self, items, parentid=None):
            created.append({"parentid": parentid, "items": items})
            if parentid is None:
                return {
                    "successful": {str(i): {"key": f"K{i}"} for i in range(len(items))},
                    "success": {str(i): f"K{i}" for i in range(len(items))},
                }
            return {"successful": {str(i): {"key": f"C{i}"} for i in range(len(items))}}

    monkeypatch.setattr(zotero_service, "_client", lambda db, user: _FakeZot())
    monkeypatch.setattr(zotero_service, "pick_collection", lambda *a, **k: "Papers")
    # Not resolvable to arXiv → the PDF link stays the paper's own URL (no network).
    monkeypatch.setattr("app.integrations.mineru.find_arxiv_pdf_by_title", lambda t: "")

    r = auth_client.post(
        "/api/zotero/upload",
        json={"project_id": pid, "paper_ids": [paper_id], "idea_ids": []},
    )
    assert r.status_code == 202, r.text
    job = auth_client.get(f"/api/jobs/{r.json()['id']}").json()
    assert job["status"] == "succeeded", job.get("error")
    assert "uploaded 1 papers" in job["log"]
    assert "2 summary/code notes + 1 PDF links" in job["log"]

    child_call = next(c for c in created if c["parentid"] == "K0")
    types = [(it.get("itemType"), it.get("linkMode")) for it in child_call["items"]]
    assert ("note", None) in types and ("attachment", "linked_url") in types
    notes = "".join(it.get("note", "") for it in child_call["items"])
    assert "memory-efficient <b>attention</b>" in notes
    assert "Code repository analysis" in notes and "github.com/foo/bar" in notes
    pdf = next(it for it in child_call["items"] if it.get("itemType") == "attachment")
    assert pdf["url"] == "https://openreview.net/pdf/x.pdf"


def test_accessible_pdf_url_prefers_arxiv(auth_client, monkeypatch) -> None:
    """An unfetchable OpenReview link resolves to the accessible arXiv link (cached on
    the document); an already-arXiv link passes through with no lookup."""
    from app.core.database import SessionLocal
    from app.models.content import Paper, PaperDocument
    from app.services import zotero_service

    pid = auth_client.post("/api/projects", json={"name": "Acc"}).json()["id"]
    db = SessionLocal()
    try:
        doc = PaperDocument(title="GTR-Bench", title_key=f"gtr-{uuid.uuid4().hex[:6]}",
                            markdown="x", extraction_method="abstract")
        db.add(doc); db.flush()
        p_or = Paper(project_id=pid, title="GTR-Bench",
                     pdf_url="https://openreview.net/pdf/x.pdf", document_id=doc.id)
        p_ax = Paper(project_id=pid, title="Other", pdf_url="https://arxiv.org/pdf/2401.1")
        db.add_all([p_or, p_ax]); db.commit()
        or_id, ax_id, doc_id = p_or.id, p_ax.id, doc.id
    finally:
        db.close()

    calls = {"n": 0}

    def fake_lookup(title):
        calls["n"] += 1
        return "https://arxiv.org/pdf/2510.07791" if "GTR" in title else ""

    monkeypatch.setattr("app.integrations.mineru.find_arxiv_pdf_by_title", fake_lookup)

    db = SessionLocal()
    try:
        p_or, p_ax = db.get(Paper, or_id), db.get(Paper, ax_id)
        assert zotero_service.accessible_pdf_url(db, p_ax) == "https://arxiv.org/pdf/2401.1"  # already arXiv
        assert zotero_service.accessible_pdf_url(db, p_or) == "https://arxiv.org/pdf/2510.07791"  # resolved
        assert db.get(PaperDocument, doc_id).resolved_pdf_url == "https://arxiv.org/pdf/2510.07791"  # cached
        zotero_service.accessible_pdf_url(db, p_or)  # second call uses the cache
        assert calls["n"] == 1  # only one lookup total (arXiv paper + cache never queried)
    finally:
        db.close()
