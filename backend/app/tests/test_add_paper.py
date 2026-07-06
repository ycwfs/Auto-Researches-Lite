"""Manually adding a paper by arXiv link or PDF upload (offline, no network).

JOB_SYNC=true makes the POST return only after the add-paper job finishes, so each
test can assert the job outcome and the resulting Paper inline.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

# pdf_url is left empty so the offline summary path never downloads anything (the
# abstract fallback runs); this keeps the test fully offline / network-free.
_FAKE_ARXIV = {
    "id": "2401.01234v1",
    "title": "A Fake Paper On Transformers",
    "authors": ["Ada Lovelace", "Alan Turing"],
    "abstract": "We study transformer attention for sequence modeling and report gains.",
    "categories": ["cs.LG"],
    "pdf_url": "",
    "published": "2024-01-02T00:00:00",
    "source": "arxiv",
}


def _new_project(client: TestClient, name: str = "AddPaper") -> int:
    return client.post("/api/projects", json={"name": name, "keywords": ["transformer"]}).json()["id"]


def test_add_paper_by_arxiv(auth_client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.integrations.auto_research.fetch_arxiv_paper", lambda _id: dict(_FAKE_ARXIV)
    )
    pid = _new_project(auth_client)
    r = auth_client.post(f"/api/projects/{pid}/discovery/papers/add", json={"url": "2401.01234"})
    assert r.status_code == 202, r.text
    job = auth_client.get(f"/api/jobs/{r.json()['id']}").json()
    assert job["status"] == "succeeded", job

    papers = auth_client.get(f"/api/projects/{pid}/discovery/papers").json()
    assert len(papers) == 1
    p = papers[0]
    assert p["title"] == _FAKE_ARXIV["title"]
    assert p["arxiv_id"] == "2401.01234v1"
    assert p["source"] == "arxiv"
    assert p["summary_en"]  # offline mock summary present
    assert p["document_id"]  # global PaperDocument (5-point summary) was built


def test_add_paper_by_arxiv_url_form(auth_client: TestClient, monkeypatch) -> None:
    """A full arxiv.org URL resolves the same way as a bare id."""
    monkeypatch.setattr(
        "app.integrations.auto_research.fetch_arxiv_paper", lambda _id: dict(_FAKE_ARXIV)
    )
    pid = _new_project(auth_client)
    r = auth_client.post(
        f"/api/projects/{pid}/discovery/papers/add",
        json={"url": "https://arxiv.org/abs/2401.01234"},
    )
    assert r.status_code == 202, r.text
    assert auth_client.get(f"/api/jobs/{r.json()['id']}").json()["status"] == "succeeded"
    assert len(auth_client.get(f"/api/projects/{pid}/discovery/papers").json()) == 1


def test_add_paper_bad_arxiv(auth_client: TestClient) -> None:
    pid = _new_project(auth_client)
    r = auth_client.post(
        f"/api/projects/{pid}/discovery/papers/add", json={"url": "https://example.com/nope"}
    )
    assert r.status_code == 400


def test_add_paper_arxiv_colon_prefix(auth_client: TestClient, monkeypatch) -> None:
    """The displayed "arXiv:NNNN.NNNNN" form (and bare arxiv: prefix) is accepted."""
    monkeypatch.setattr(
        "app.integrations.auto_research.fetch_arxiv_paper", lambda _id: dict(_FAKE_ARXIV)
    )
    pid = _new_project(auth_client)
    r = auth_client.post(
        f"/api/projects/{pid}/discovery/papers/add", json={"url": "arXiv:2401.01234"}
    )
    assert r.status_code == 202, r.text
    assert auth_client.get(f"/api/jobs/{r.json()['id']}").json()["status"] == "succeeded"


def test_parse_arxiv_id_forms() -> None:
    from app.integrations.auto_research import parse_arxiv_id

    assert parse_arxiv_id("arXiv:2401.01234") == "2401.01234"
    assert parse_arxiv_id("arxiv: 2401.01234v2") == "2401.01234v2"
    assert parse_arxiv_id("https://arxiv.org/pdf/2401.01234v1.pdf") == "2401.01234v1"
    assert parse_arxiv_id("cond-mat/9901001") == "cond-mat/9901001"
    assert parse_arxiv_id("not a paper") is None


def test_add_paper_dedup(auth_client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.integrations.auto_research.fetch_arxiv_paper", lambda _id: dict(_FAKE_ARXIV)
    )
    pid = _new_project(auth_client)
    first = auth_client.post(f"/api/projects/{pid}/discovery/papers/add", json={"url": "2401.01234"})
    assert auth_client.get(f"/api/jobs/{first.json()['id']}").json()["status"] == "succeeded"
    # Re-adding the same id is a friendly no-op, not a second Paper.
    second = auth_client.post(f"/api/projects/{pid}/discovery/papers/add", json={"url": "2401.01234v2"})
    job2 = auth_client.get(f"/api/jobs/{second.json()['id']}").json()
    assert job2["status"] == "succeeded", job2
    assert "already in the project" in job2["log"].lower()
    assert len(auth_client.get(f"/api/projects/{pid}/discovery/papers").json()) == 1


def test_add_paper_by_pdf(auth_client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.integrations.mineru.extract_from_bytes",
        lambda _data: "Deep Residual Learning\n\nAbstract\nWe present residual networks for vision.",
    )
    pid = _new_project(auth_client)
    r = auth_client.post(
        f"/api/projects/{pid}/discovery/papers/upload",
        files={"file": ("resnet.pdf", b"%PDF-1.4 fake bytes", "application/pdf")},
        data={"title": "Deep Residual Learning"},
    )
    assert r.status_code == 202, r.text
    job = auth_client.get(f"/api/jobs/{r.json()['id']}").json()
    assert job["status"] == "succeeded", job

    papers = auth_client.get(f"/api/projects/{pid}/discovery/papers").json()
    assert len(papers) == 1
    p = papers[0]
    assert p["title"] == "Deep Residual Learning"
    assert p["source"] == "upload"
    assert p["summary_en"]
    assert p["document_id"]


def test_upload_deletes_temp_file(auth_client: TestClient, monkeypatch) -> None:
    """The raw uploaded PDF is removed after extraction (no disk leak)."""
    monkeypatch.setattr(
        "app.integrations.mineru.extract_from_bytes", lambda _d: "A Paper Title\n\nbody text here"
    )
    pid = _new_project(auth_client)
    r = auth_client.post(
        f"/api/projects/{pid}/discovery/papers/upload",
        files={"file": ("x.pdf", b"%PDF-1.4 bytes", "application/pdf")},
    )
    assert auth_client.get(f"/api/jobs/{r.json()['id']}").json()["status"] == "succeeded"
    from app.core.database import SessionLocal
    from app.core.paths import uploads_dir
    from app.models.project import Project

    db = SessionLocal()
    try:
        owner = db.get(Project, pid).owner_id
    finally:
        db.close()
    assert list(uploads_dir(owner, pid).glob("*.pdf")) == []


def test_upload_rejects_non_pdf(auth_client: TestClient) -> None:
    pid = _new_project(auth_client)
    r = auth_client.post(
        f"/api/projects/{pid}/discovery/papers/upload",
        files={"file": ("notes.txt", b"just text", "text/plain")},
    )
    assert r.status_code == 415


def test_upload_empty_text_fails_job(auth_client: TestClient, monkeypatch) -> None:
    """A scanned/image-only PDF (no extractable text) fails the job with a clear message."""
    monkeypatch.setattr("app.integrations.mineru.extract_from_bytes", lambda _data: "   ")
    pid = _new_project(auth_client)
    r = auth_client.post(
        f"/api/projects/{pid}/discovery/papers/upload",
        files={"file": ("scan.pdf", b"%PDF-1.4 image only", "application/pdf")},
    )
    assert r.status_code == 202, r.text
    job = auth_client.get(f"/api/jobs/{r.json()['id']}").json()
    assert job["status"] == "failed"
    assert "extract text" in job["error"].lower()
    assert auth_client.get(f"/api/projects/{pid}/discovery/papers").json() == []
