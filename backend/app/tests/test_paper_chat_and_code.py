"""Per-paper chat on the Discover panel + manual code analysis (user-supplied repo URL)."""
from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.core.database import SessionLocal


def _seed_paper_with_doc(
    pid: int, *, markdown: str = "", summary: str = "", extraction: str | None = None
) -> int:
    """A discovered Paper linked to a PaperDocument (unique title to dodge global dedup)."""
    from app.models.content import Paper, PaperDocument

    tag = uuid.uuid4().hex[:8]
    db = SessionLocal()
    try:
        doc = PaperDocument(
            title=f"Chatty attention paper {tag}",
            title_key=f"chatty-attention-{tag}",
            abstract="A study of efficient attention.",
            markdown=markdown,
            summary=summary,
            extraction_method=extraction if extraction is not None else ("mineru" if markdown else ""),
        )
        db.add(doc)
        db.flush()
        paper = Paper(
            project_id=pid,
            title=doc.title,
            abstract=doc.abstract,
            source="arxiv",
            document_id=doc.id,
        )
        db.add(paper)
        db.commit()
        return paper.id
    finally:
        db.close()


def test_paper_chat_grounded_in_fulltext_and_summary(auth_client: TestClient) -> None:
    pid = auth_client.post(
        "/api/projects", json={"name": "PaperChat", "keywords": ["attention"]}
    ).json()["id"]
    paper_id = _seed_paper_with_doc(
        pid,
        markdown="# Chatty attention paper\nThe SECRET-TOKEN-XYZ method halves memory.",
        summary="- Contribution: memory-efficient attention.",
    )

    q = f"?scope=discovered&scope_id={paper_id}"
    assert auth_client.get(f"/api/projects/{pid}/chat{q}").json() == []

    r = auth_client.post(f"/api/projects/{pid}/chat{q}", json={"message": "What is the method?"})
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "assistant" and r.json()["content"].strip()
    history = auth_client.get(f"/api/projects/{pid}/chat{q}").json()
    assert [m["role"] for m in history] == ["user", "assistant"]

    # The chat context is the paper's own: summary + MinerU full text.
    ctx = auth_client.get(f"/api/projects/{pid}/entity-context/discovered/{paper_id}").json()
    assert "SECRET-TOKEN-XYZ" in ctx["content"]  # full text present
    assert "memory-efficient attention" in ctx["content"]  # summary present

    # Scope guards: foreign/unknown ids 404, unknown scope 400.
    assert auth_client.post(
        f"/api/projects/{pid}/chat?scope=discovered&scope_id=999999",
        json={"message": "x"},
    ).status_code == 404
    assert auth_client.post(
        f"/api/projects/{pid}/chat?scope=bogus&scope_id={paper_id}", json={"message": "x"}
    ).status_code == 400

    # has_fulltext is surfaced on the paper list for the frontend gate.
    papers = auth_client.get(f"/api/projects/{pid}/discovery/papers").json()
    assert next(pp for pp in papers if pp["id"] == paper_id)["has_fulltext"] is True


def test_paper_chat_gated_without_fulltext(auth_client: TestClient) -> None:
    """A paper with only the abstract fallback (no MinerU/pypdf text) can't be chatted."""
    pid = auth_client.post(
        "/api/projects", json={"name": "NoFullText", "keywords": ["attention"]}
    ).json()["id"]
    paper_id = _seed_paper_with_doc(pid, markdown="just the abstract", extraction="abstract")

    papers = auth_client.get(f"/api/projects/{pid}/discovery/papers").json()
    assert next(pp for pp in papers if pp["id"] == paper_id)["has_fulltext"] is False
    r = auth_client.post(
        f"/api/projects/{pid}/chat?scope=discovered&scope_id={paper_id}",
        json={"message": "hi"},
    )
    assert r.status_code == 409 and "full text" in r.json()["detail"]


def test_manual_code_analysis_with_user_url(auth_client: TestClient, monkeypatch) -> None:
    from app.services import code_repo

    pid = auth_client.post(
        "/api/projects", json={"name": "ManualCode", "keywords": ["x"]}
    ).json()["id"]
    paper_id = _seed_paper_with_doc(pid, markdown="No repo link in this text.")

    seen: dict = {}

    def fake_analyze(url, llm, prompt=None):
        seen["url"] = url
        return url, "MANUAL ANALYSIS: training loop in train.py"

    monkeypatch.setattr(code_repo, "analyze", fake_analyze)

    # Invalid URL rejected before any job is created.
    r = auth_client.post(
        f"/api/projects/{pid}/discovery/papers/{paper_id}/recode",
        json={"repo_url": "not a repository"},
    )
    assert r.status_code == 400

    # A deep GitHub link is normalized to owner/repo and analyzed directly.
    r = auth_client.post(
        f"/api/projects/{pid}/discovery/papers/{paper_id}/recode",
        json={"repo_url": "https://github.com/foo/bar/tree/main/src"},
    )
    assert r.status_code == 202, r.text
    job = auth_client.get(f"/api/jobs/{r.json()['id']}").json()
    assert job["status"] == "succeeded", job.get("error")
    assert seen["url"] == "https://github.com/foo/bar"

    s = auth_client.get(f"/api/projects/{pid}/discovery/papers/{paper_id}/summary").json()
    assert s["code_status"] == "ok"
    assert s["code_url"] == "https://github.com/foo/bar"
    assert "MANUAL ANALYSIS" in s["code_summary"]


def test_bulk_paper_delete_purges_chat(auth_client: TestClient) -> None:
    from app.models.context import ChatMessage

    pid = auth_client.post(
        "/api/projects", json={"name": "PurgeChat", "keywords": ["x"]}
    ).json()["id"]
    paper_id = _seed_paper_with_doc(pid, markdown="text")
    r = auth_client.post(
        f"/api/projects/{pid}/chat?scope=discovered&scope_id={paper_id}",
        json={"message": "hello"},
    )
    assert r.status_code == 200

    r = auth_client.post(
        f"/api/projects/{pid}/discovery/papers/delete", json={"paper_ids": [paper_id]}
    )
    assert r.status_code == 200 and r.json()["deleted"] == 1
    db = SessionLocal()
    try:
        left = (
            db.query(ChatMessage)
            .filter(ChatMessage.scope == "discovered", ChatMessage.scope_id == paper_id)
            .count()
        )
        assert left == 0  # the bulk delete purged the paper's chat thread
    finally:
        db.close()


def test_normalize_repo_url() -> None:
    from app.services.code_repo import normalize_repo_url

    assert normalize_repo_url("https://github.com/foo/bar/tree/main/x") == "https://github.com/foo/bar"
    assert normalize_repo_url("github.com/foo/bar.git") == "https://github.com/foo/bar"
    assert normalize_repo_url("https://gitlab.com/grp/sub/proj/-/tree/main") == "https://gitlab.com/grp/sub/proj"
    assert normalize_repo_url("https://example.com/foo/bar") is None
    assert normalize_repo_url("nonsense") is None


def test_paper_chat_context_is_uncapped(auth_client: TestClient) -> None:
    """The paper-chat context includes the FULL parsed text — no 40K cap, no marker."""
    from app.core.database import SessionLocal
    from app.models.content import Paper
    from app.services import context_service

    pid = auth_client.post(
        "/api/projects", json={"name": "FullCtx", "keywords": ["x"]}
    ).json()["id"]
    big = "para. " * 12000  # ~72K chars, well past the old 40K cap
    marker = "TAILMARKER-9Z"
    paper_id = _seed_paper_with_doc(pid, markdown=big + marker)

    db = SessionLocal()
    try:
        paper = db.get(Paper, paper_id)
        project = paper.project  # type: ignore[attr-defined]
        ctx = context_service.build_discovered_paper_context(db, project, paper)
        assert marker in ctx  # the tail survives — full text, uncapped
        assert "(truncated)" not in ctx
    finally:
        db.close()


def test_extract_sources_keeps_all_papers_full_text(tmp_path) -> None:
    """extract_sources defaults to every paper at full length (no top_n / per_cap)."""
    from app.services import fulltext

    # src is a paper-like dict; with no pdf_url extract() uses the abstract text.
    items = [
        (f"Paper {i}", {"title": f"Paper {i}", "abstract": "x" * 50000 + f"END{i}"})
        for i in range(20)  # 20 > the old 15-paper cap
    ]
    out = fulltext.extract_sources(items, tmp_path)
    assert len(out) == 20  # all papers, not just 15
    assert out[0]["text"].endswith("END0")  # >40K text kept in full
    assert len(out[0]["text"]) > 40000  # past the old 40K per-paper cap
