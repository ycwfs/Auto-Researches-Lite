"""Per-project Semantic Scholar settings flow through the project API."""
from __future__ import annotations

from fastapi.testclient import TestClient


def test_project_name_and_keywords_update(auth_client: TestClient) -> None:
    """Name + keywords are editable (to tune paper retrieval) and persist."""
    p = auth_client.post("/api/projects", json={"name": "Old", "keywords": ["a"]}).json()
    r = auth_client.patch(
        f"/api/projects/{p['id']}",
        json={"name": "New Name", "keywords": ["sparse attention", "kv cache"]},
    )
    assert r.status_code == 200
    assert r.json()["name"] == "New Name"
    assert r.json()["keywords"] == ["sparse attention", "kv cache"]
    got = auth_client.get(f"/api/projects/{p['id']}").json()
    assert got["name"] == "New Name" and got["keywords"] == ["sparse attention", "kv cache"]


def test_project_paper_finder_query_roundtrip_and_default(auth_client: TestClient) -> None:
    """The AI Paper Finder query persists on create + update, and defaults to "" when omitted."""
    # Omitted on create -> defaults to "" (the source falls back to keywords+name).
    p = auth_client.post("/api/projects", json={"name": "PFQ"}).json()
    assert p["paper_finder_query"] == ""

    # Created with an explicit query (e.g. a pasted abstract) -> returned verbatim.
    abstract = "We introduce a selective state space model for linear-time sequence modeling."
    p2 = auth_client.post(
        "/api/projects", json={"name": "PFQ2", "paper_finder_query": abstract}
    ).json()
    assert p2["paper_finder_query"] == abstract

    # Editable via PATCH and persisted.
    r = auth_client.patch(f"/api/projects/{p['id']}", json={"paper_finder_query": abstract})
    assert r.status_code == 200 and r.json()["paper_finder_query"] == abstract
    assert auth_client.get(f"/api/projects/{p['id']}").json()["paper_finder_query"] == abstract


def test_project_paper_finder_min_score_roundtrip_default_and_range(auth_client: TestClient) -> None:
    """The AI Paper Finder relevance threshold defaults to 0, persists, and is range-checked."""
    p = auth_client.post("/api/projects", json={"name": "PFScore"}).json()
    assert p["paper_finder_min_score"] == 0.0  # off by default

    r = auth_client.patch(f"/api/projects/{p['id']}", json={"paper_finder_min_score": 0.6})
    assert r.status_code == 200 and r.json()["paper_finder_min_score"] == 0.6
    assert auth_client.get(f"/api/projects/{p['id']}").json()["paper_finder_min_score"] == 0.6

    # Out of range is rejected (0..1).
    assert auth_client.patch(f"/api/projects/{p['id']}", json={"paper_finder_min_score": 1.5}).status_code == 422
    assert auth_client.patch(f"/api/projects/{p['id']}", json={"paper_finder_min_score": -0.1}).status_code == 422


def test_project_s2_settings_create_update_and_defaults(auth_client: TestClient) -> None:
    # Create with explicit S2 settings.
    r = auth_client.post(
        "/api/projects",
        json={
            "name": "S2 tuning",
            "categories": ["cs.LG"],
            "s2_recency_days": 90,
            "s2_min_citations": 3,
            "s2_fields_of_study": "off",
        },
    )
    assert r.status_code == 201, r.text
    p = r.json()
    assert (p["s2_recency_days"], p["s2_min_citations"], p["s2_fields_of_study"]) == (90, 3, "off")

    # Patch a subset; others stay put.
    r = auth_client.patch(f"/api/projects/{p['id']}", json={"s2_recency_days": 0})
    assert r.status_code == 200, r.text
    assert r.json()["s2_recency_days"] == 0
    assert r.json()["s2_min_citations"] == 3

    # Omitted on create -> defaults that mirror the env fallbacks.
    r = auth_client.post("/api/projects", json={"name": "defaults", "categories": ["cs.AI"]})
    assert r.status_code == 201, r.text
    p2 = r.json()
    assert (p2["s2_recency_days"], p2["s2_min_citations"], p2["s2_fields_of_study"]) == (365, 0, "")


def test_project_max_total_papers_default_update_and_range(auth_client: TestClient) -> None:
    p = auth_client.post("/api/projects", json={"name": "cap", "categories": ["cs.AI"]}).json()
    assert p["max_total_papers"] == 600  # default cap keeps the DB bounded
    r = auth_client.patch(f"/api/projects/{p['id']}", json={"max_total_papers": 500})
    assert r.status_code == 200, r.text
    assert r.json()["max_total_papers"] == 500
    # Range is 0..600 — larger is rejected.
    assert auth_client.patch(f"/api/projects/{p['id']}", json={"max_total_papers": 700}).status_code == 422


def test_project_s2_validation_rejects_negative(auth_client: TestClient) -> None:
    r = auth_client.post("/api/projects", json={"name": "bad", "s2_recency_days": -1})
    assert r.status_code == 422
