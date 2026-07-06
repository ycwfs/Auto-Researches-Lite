"""Stage 1 discovery end-to-end test (offline LLM, real arXiv fetch)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def test_paper_finder_run_is_decoupled(auth_client: TestClient, monkeypatch) -> None:
    """Regular discovery (and the scheduler, same path) excludes ai_paper_finder; the
    dedicated /run/paper-finder route fetches ONLY ai_paper_finder."""
    import app.services.discovery_service as ds

    calls: list[list[str]] = []

    def fake_fetch(keys, query, *a, **k):
        calls.append(list(keys))
        return []

    monkeypatch.setattr(ds, "fetch_from_sources", fake_fetch)

    pid = auth_client.post(
        "/api/projects",
        json={
            "name": "Decouple",
            "paper_sources": ["arxiv", "semantic_scholar", "ai_paper_finder"],
            "paper_finder_query": "selective state space models",
        },
    ).json()["id"]

    # Regular run is a 'discovery' job and drops ai_paper_finder (JOB_SYNC → completes here).
    rr = auth_client.post(f"/api/projects/{pid}/discovery/run")
    assert rr.status_code == 202 and rr.json()["type"] == "discovery"
    assert "ai_paper_finder" not in calls[-1] and "arxiv" in calls[-1]

    # The dedicated button is its OWN 'paper_finder' job (so it runs concurrently) and
    # fetches ONLY the AI Paper Finder.
    pf = auth_client.post(f"/api/projects/{pid}/discovery/run/paper-finder")
    assert pf.status_code == 202 and pf.json()["type"] == "paper_finder"
    assert calls[-1] == ["ai_paper_finder"]


def test_finder_score_persisted_and_exposed(auth_client: TestClient, monkeypatch) -> None:
    """A paper fetched from the AI Paper Finder carries its semantic cosine score all the
    way through to the discovery API (papers from other sources stay at 0)."""
    import app.services.discovery_service as ds

    def fake_fetch(keys, query, *a, **k):
        if list(keys) == ["ai_paper_finder"]:
            return [{
                "id": "or:42", "title": "Selective SSM", "abstract": "ssm",
                "authors": [], "categories": [], "pdf_url": "", "published": "2026",
                "venue": "ICLR", "source": "ai_paper_finder", "finder_score": 0.83,
            }]
        return []

    monkeypatch.setattr(ds, "fetch_from_sources", fake_fetch)

    pid = auth_client.post(
        "/api/projects",
        json={
            "name": "Score",
            "paper_sources": ["ai_paper_finder"],
            "paper_finder_query": "selective state space models",
        },
    ).json()["id"]

    pf = auth_client.post(f"/api/projects/{pid}/discovery/run/paper-finder")
    assert pf.status_code == 202

    papers = auth_client.get(f"/api/projects/{pid}/discovery/papers").json()
    hit = next((p for p in papers if p["title"] == "Selective SSM"), None)
    assert hit is not None and round(hit["finder_score"], 2) == 0.83


def test_papers_with_null_finder_score_still_serialize(auth_client: TestClient) -> None:
    """A paper discovered before the finder_score column existed has finder_score NULL
    (the additive migration doesn't backfill). It must still serialize — a non-nullable
    float field would 500 the entire papers list and hide every paper."""
    from app.core.database import SessionLocal
    from app.models.content import Paper

    pid = auth_client.post("/api/projects", json={"name": "NullScore"}).json()["id"]
    db = SessionLocal()
    try:
        db.add(Paper(project_id=pid, title="Legacy", source="arxiv", finder_score=None))
        db.commit()
    finally:
        db.close()

    r = auth_client.get(f"/api/projects/{pid}/discovery/papers")
    assert r.status_code == 200
    assert r.json()[0]["finder_score"] == 0.0


def test_bulk_delete_selected_papers(auth_client: TestClient) -> None:
    """Bulk delete removes the given papers, ignores ids from other projects, and reports
    the count."""
    from app.core.database import SessionLocal
    from app.models.content import Paper

    pid = auth_client.post("/api/projects", json={"name": "Bulk"}).json()["id"]
    other = auth_client.post("/api/projects", json={"name": "Other"}).json()["id"]
    db = SessionLocal()
    try:
        ours = [Paper(project_id=pid, title=f"P{i}", source="arxiv") for i in range(3)]
        foreign = Paper(project_id=other, title="X", source="arxiv")
        db.add_all([*ours, foreign])
        db.commit()
        ids = [p.id for p in ours]
        foreign_id = foreign.id
    finally:
        db.close()

    # Delete 2 of our 3, plus a foreign id that must be silently ignored.
    r = auth_client.post(
        f"/api/projects/{pid}/discovery/papers/delete",
        json={"paper_ids": ids[:2] + [foreign_id]},
    )
    assert r.status_code == 200 and r.json()["deleted"] == 2
    remaining = [p["id"] for p in auth_client.get(f"/api/projects/{pid}/discovery/papers").json()]
    assert remaining == [ids[2]]
    # The other project's paper is untouched.
    assert [p["title"] for p in auth_client.get(f"/api/projects/{other}/discovery/papers").json()] == ["X"]


@pytest.mark.network
def test_discovery_end_to_end(auth_client: TestClient) -> None:
    r = auth_client.post(
        "/api/projects",
        json={"name": "Disc", "categories": ["cs.LG"], "keywords": ["transformer"], "max_results": 5},
    )
    assert r.status_code == 201
    pid = r.json()["id"]

    # JOB_SYNC=true → this returns only after the job finishes.
    run = auth_client.post(f"/api/projects/{pid}/discovery/run")
    assert run.status_code == 202
    job_id = run.json()["id"]

    job = auth_client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] in {"succeeded", "failed"}, job
    assert job["status"] == "succeeded", job["error"]

    papers = auth_client.get(f"/api/projects/{pid}/discovery/papers").json()
    assert len(papers) >= 1
    assert papers[0]["summary_en"]  # mock summary present

    # Ideas are now a separate scheduled step (see test_ideas), not part of discovery.

    trends = auth_client.get(f"/api/projects/{pid}/discovery/trends").json()
    assert trends["paper_count"] >= 1
    assert len(trends["top_keywords"]) >= 1

    wc = auth_client.get(f"/api/projects/{pid}/discovery/wordcloud")
    assert wc.status_code == 200
    assert wc.headers["content-type"] == "image/png"


def test_paper_source_exposed_and_deletable(auth_client: TestClient) -> None:
    """Papers expose their source channel and can be deleted from the project."""
    from app.core.database import SessionLocal
    from app.models.content import Paper

    pid = auth_client.post("/api/projects", json={"name": "DelProj"}).json()["id"]
    db = SessionLocal()
    try:
        a = Paper(project_id=pid, title="A", source="arxiv", arxiv_id="2401.001")
        b = Paper(project_id=pid, title="B", source="semantic_scholar")
        db.add_all([a, b])
        db.commit()
        aid = a.id
    finally:
        db.close()

    papers = auth_client.get(f"/api/projects/{pid}/discovery/papers").json()
    by_title = {p["title"]: p for p in papers}
    assert by_title["A"]["source"] == "arxiv"
    assert by_title["B"]["source"] == "semantic_scholar"

    # Delete one paper → it's gone; deleting again → 404.
    assert auth_client.delete(f"/api/projects/{pid}/discovery/papers/{aid}").status_code == 204
    titles = [p["title"] for p in auth_client.get(f"/api/projects/{pid}/discovery/papers").json()]
    assert titles == ["B"]
    assert auth_client.delete(f"/api/projects/{pid}/discovery/papers/{aid}").status_code == 404
