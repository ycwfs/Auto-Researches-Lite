"""Per-paper re-summarize / re-analyze (prompt-debug) jobs + the force re-run."""
from __future__ import annotations

from fastapi.testclient import TestClient


def _paper_with_doc(pid: int):
    from app.core.database import SessionLocal
    from app.models.content import Paper, PaperDocument

    db = SessionLocal()
    try:
        doc = PaperDocument(
            title="T", markdown="# Full text\n" + "content line\n" * 50,
            summary="OLD SUMMARY", summary_model="mock",
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)
        paper = Paper(project_id=pid, title="T", source="arxiv", arxiv_id="2401.00001", document_id=doc.id)
        db.add(paper)
        db.commit()
        return paper.id, doc.id
    finally:
        db.close()


def test_resummarize_writes_project_override_not_shared_doc(auth_client: TestClient) -> None:
    """Re-summarize regenerates THIS project's view (a per-project override) while leaving
    the SHARED document summary untouched — so it can't change another user's summary."""
    from app.core.database import SessionLocal
    from app.models.content import PaperDocument

    pid = auth_client.post("/api/projects", json={"name": "Resum"}).json()["id"]
    paper_id, doc_id = _paper_with_doc(pid)

    r = auth_client.post(f"/api/projects/{pid}/discovery/papers/{paper_id}/resummarize")
    assert r.status_code == 202 and r.json()["type"] == "resummarize"
    job = auth_client.get(f"/api/jobs/{r.json()['id']}").json()
    assert job["status"] == "succeeded", job.get("error")

    # the project's view (card endpoint) shows the regenerated summary…
    view = auth_client.get(f"/api/projects/{pid}/discovery/papers/{paper_id}/summary").json()
    assert view["summary_5pt"] and view["summary_5pt"] != "OLD SUMMARY"
    # …but the SHARED document summary is unchanged (isolation).
    db = SessionLocal()
    try:
        assert db.get(PaperDocument, doc_id).summary == "OLD SUMMARY"
    finally:
        db.close()


def test_resummarize_isolated_across_projects(auth_client: TestClient) -> None:
    """Two projects share one PaperDocument; project A re-summarizes → only A's view
    changes, B still sees the shared default. This is the cross-user isolation guarantee."""
    from app.core.database import SessionLocal
    from app.models.content import Paper, PaperDocument

    a = auth_client.post("/api/projects", json={"name": "A"}).json()["id"]
    b = auth_client.post("/api/projects", json={"name": "B"}).json()["id"]
    db = SessionLocal()
    try:
        doc = PaperDocument(
            title="Shared", markdown="# text\n" + "line\n" * 40,
            summary="SHARED DEFAULT", summary_model="mock",
        )
        db.add(doc)
        db.commit()
        pa = Paper(project_id=a, title="Shared", source="arxiv", arxiv_id="2401.09", document_id=doc.id)
        pb = Paper(project_id=b, title="Shared", source="arxiv", arxiv_id="2401.09", document_id=doc.id)
        db.add_all([pa, pb])
        db.commit()
        pa_id, pb_id = pa.id, pb.id
    finally:
        db.close()

    r = auth_client.post(f"/api/projects/{a}/discovery/papers/{pa_id}/resummarize")
    assert auth_client.get(f"/api/jobs/{r.json()['id']}").json()["status"] == "succeeded"

    va = auth_client.get(f"/api/projects/{a}/discovery/papers/{pa_id}/summary").json()
    vb = auth_client.get(f"/api/projects/{b}/discovery/papers/{pb_id}/summary").json()
    assert va["summary_5pt"] != "SHARED DEFAULT"   # A regenerated its own
    assert vb["summary_5pt"] == "SHARED DEFAULT"   # B is untouched


def test_custom_summary_dedups_unless_forced(auth_client: TestClient) -> None:
    """A custom-prompt project's per-paper override is computed ONCE and reused on later
    discovery/idea runs (dedup); only force (the re-summarize button) recomputes it."""
    from app.core.database import SessionLocal
    from app.models.content import PaperDocument
    from app.services import paper_db
    from app.services.llm import LLMService

    pid = auth_client.post("/api/projects", json={"name": "Dedup"}).json()["id"]
    db = SessionLocal()
    try:
        doc = PaperDocument(title="T", markdown="# t\n" + "body\n" * 30)
        db.add(doc)
        db.commit()
        llm = LLMService()  # offline mock
        ov = paper_db.set_project_summary(db, pid, doc, llm, "custom prompt")
        assert (ov.summary or "").strip()
        ov.summary = "SENTINEL"
        db.commit()
        paper_db.set_project_summary(db, pid, doc, llm, "custom prompt")  # dedup -> no recompute
        assert ov.summary == "SENTINEL"
        paper_db.set_project_summary(db, pid, doc, llm, "custom prompt", force=True)  # recompute
        assert ov.summary != "SENTINEL"
    finally:
        db.close()


def test_bulk_resummarize_selected(auth_client: TestClient) -> None:
    """POST /papers/resummarize regenerates the Summary for ALL selected papers in one job
    (each as this project's own override); ids from another project are scoped out."""
    from app.core.database import SessionLocal
    from app.models.content import Paper, PaperDocument

    pid = auth_client.post("/api/projects", json={"name": "Bulk"}).json()["id"]
    other = auth_client.post("/api/projects", json={"name": "Other"}).json()["id"]
    ids = []
    db = SessionLocal()
    try:
        for i in range(3):
            doc = PaperDocument(title=f"D{i}", markdown="# t\n" + "body\n" * 30, summary="OLD", summary_model="mock")
            db.add(doc)
            db.commit()
            p = Paper(project_id=pid, title=f"D{i}", source="arxiv", arxiv_id=f"2401.{i}", document_id=doc.id)
            db.add(p)
            db.commit()
            ids.append(p.id)
        fdoc = PaperDocument(title="F", markdown="# t\nx", summary="OLD", summary_model="mock")
        db.add(fdoc)
        db.commit()
        fp = Paper(project_id=other, title="F", source="arxiv", document_id=fdoc.id)
        db.add(fp)
        db.commit()
        foreign_id = fp.id
    finally:
        db.close()

    r = auth_client.post(
        f"/api/projects/{pid}/discovery/papers/resummarize",
        json={"paper_ids": ids + [foreign_id], "mode": "full_text"},
    )
    assert r.status_code == 202 and r.json()["type"] == "resummarize"
    job = auth_client.get(f"/api/jobs/{r.json()['id']}").json()
    assert job["status"] == "succeeded", job.get("error")

    for paper_id in ids:  # all of THIS project's selected papers regenerated
        v = auth_client.get(f"/api/projects/{pid}/discovery/papers/{paper_id}/summary").json()
        assert v["summary_5pt"] and v["summary_5pt"] != "OLD"
    # the foreign paper was scoped out (other project's view unchanged)
    vf = auth_client.get(f"/api/projects/{other}/discovery/papers/{foreign_id}/summary").json()
    assert vf["summary_5pt"] == "OLD"


def test_recode_runs_and_404s(auth_client: TestClient) -> None:
    """POST .../recode enqueues a code re-analysis job (succeeds even with no repo in the
    text); a foreign/missing paper id is 404."""
    pid = auth_client.post("/api/projects", json={"name": "Resum2"}).json()["id"]
    paper_id, _ = _paper_with_doc(pid)

    rc = auth_client.post(f"/api/projects/{pid}/discovery/papers/{paper_id}/recode")
    assert rc.status_code == 202 and rc.json()["type"] == "resummarize"
    jc = auth_client.get(f"/api/jobs/{rc.json()['id']}").json()
    assert jc["status"] == "succeeded", jc.get("error")

    assert auth_client.post(f"/api/projects/{pid}/discovery/papers/999999/resummarize").status_code == 404
    assert auth_client.post(f"/api/projects/{pid}/discovery/papers/999999/recode").status_code == 404


def test_force_resummarize_unit() -> None:
    """ensure_summarized(force=True) re-runs even when a summary already exists."""
    from pathlib import Path

    from app.core.database import SessionLocal
    from app.models.content import PaperDocument
    from app.services import paper_db
    from app.services.llm import LLMService

    db = SessionLocal()
    try:
        doc = PaperDocument(title="T", markdown="# text\nbody", summary="prev", summary_model="mock")
        db.add(doc)
        db.commit()
        llm = LLMService()  # offline mock
        paper_db.ensure_summarized(db, doc, llm, Path("/tmp"), force=True)
        assert doc.summary and doc.summary != "prev"
        # without force, an existing summary is left untouched
        doc.summary = "kept"
        paper_db.ensure_summarized(db, doc, llm, Path("/tmp"), force=False)
        assert doc.summary == "kept"
    finally:
        db.close()
