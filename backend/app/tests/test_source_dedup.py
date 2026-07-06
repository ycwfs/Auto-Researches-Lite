"""Cross-source dedup (same paper from different sources) + conference (venue) display."""
from __future__ import annotations

from fastapi.testclient import TestClient


def test_cross_source_dedup_and_venue_merge(monkeypatch) -> None:
    """The same paper from arXiv (arXiv id) and AI Paper Finder (content-hash id) is
    deduped by normalized title, and the conference label is carried onto the kept one."""
    from app.integrations import sources
    from app.integrations.sources.base import Source, SourceQuery

    class _Fake(Source):
        def __init__(self, key, papers):
            self.key = key
            self._papers = papers

        def fetch(self, query):
            return [dict(p) for p in self._papers]

    arx = _Fake("arx", [{"id": "2401.0001", "title": "A Great Paper", "authors": [], "abstract": "x"}])
    pf = _Fake(
        "pf",
        [
            # same paper, different id + punctuation in title + carries the venue
            {"id": "hash1", "title": "A Great Paper!", "venue": "CVPR", "published": "2026", "authors": [], "abstract": "x"},
            {"id": "hash2", "title": "Only In Paper Finder", "venue": "ICLR", "published": "2026", "authors": [], "abstract": "y"},
        ],
    )
    monkeypatch.setattr(sources, "SOURCE_REGISTRY", {"arx": arx, "pf": pf})

    out = sources.fetch_from_sources(["arx", "pf"], SourceQuery())
    assert len(out) == 2  # the shared paper collapses to one; the PF-only one stays
    great = next(p for p in out if "Great" in p["title"])
    assert great["source"] == "arx"  # arX fetched first → kept
    assert great["venue"] == "CVPR"  # ...but enriched with the conference from the PF duplicate
    assert great["published"] == "2026"  # year filled even though arX had no published key


def _seed_discovery_job(pid: int):
    from app.core.database import SessionLocal
    from app.models.enums import JobType
    from app.models.job import Job
    from app.models.project import Project

    db = SessionLocal()
    try:
        owner = db.get(Project, pid).owner_id
        job = Job(project_id=pid, user_id=owner, type=JobType.discovery)
        db.add(job)
        db.commit()
        db.refresh(job)
        return job.id
    finally:
        db.close()


def test_cross_run_enriches_existing_paper_with_venue(auth_client: TestClient, monkeypatch) -> None:
    """A paper first stored from arXiv (no venue) gets its conference filled when a later
    run returns the same paper from AI Paper Finder — instead of the venue being lost."""
    from app.core.database import SessionLocal
    from app.models.content import Paper
    from app.services import discovery_service

    pid = auth_client.post("/api/projects", json={"name": "Enrich"}).json()["id"]
    db = SessionLocal()
    try:
        db.add(Paper(project_id=pid, arxiv_id="2401.55555", title="A Shared Paper", source="arxiv", pdf_url=""))
        db.commit()
    finally:
        db.close()
    # Next run: AI Paper Finder returns the SAME paper (content-hash id) with a venue.
    monkeypatch.setattr(
        "app.services.discovery_service.fetch_from_sources",
        lambda *a, **k: [{"id": "abc123hash", "title": "A Shared Paper!", "venue": "CVPR",
                          "published": "2026", "source": "ai_paper_finder", "authors": [], "abstract": "x", "pdf_url": ""}],
    )
    jid = _seed_discovery_job(pid)
    db = SessionLocal()
    try:
        discovery_service.run_discovery(db, jid)
        db.expire_all()
        rows = db.query(Paper).filter(Paper.project_id == pid).all()
        assert len(rows) == 1  # not duplicated
        assert rows[0].venue == "CVPR"  # existing arXiv row enriched with the conference
    finally:
        db.close()


def test_distinct_arxiv_same_title_not_dropped(auth_client: TestClient, monkeypatch) -> None:
    """A different arXiv paper that happens to share a title is kept (own arXiv id), so the
    title dedup never silently loses a distinct paper."""
    from app.core.database import SessionLocal
    from app.models.content import Paper
    from app.services import discovery_service

    pid = auth_client.post("/api/projects", json={"name": "NoDrop"}).json()["id"]
    db = SessionLocal()
    try:
        db.add(Paper(project_id=pid, arxiv_id="2401.00001", title="Common Title", source="arxiv", pdf_url=""))
        db.commit()
    finally:
        db.close()
    monkeypatch.setattr(
        "app.services.discovery_service.fetch_from_sources",
        lambda *a, **k: [{"id": "2509.99999", "title": "Common Title", "source": "arxiv",
                          "authors": [], "abstract": "y", "pdf_url": ""}],
    )
    jid = _seed_discovery_job(pid)
    db = SessionLocal()
    try:
        discovery_service.run_discovery(db, jid)
        db.expire_all()
        rows = db.query(Paper).filter(Paper.project_id == pid).all()
        assert len(rows) == 2  # both distinct arXiv papers kept despite identical titles
    finally:
        db.close()


def test_paper_venue_surfaces_in_api(auth_client: TestClient) -> None:
    from app.core.database import SessionLocal
    from app.models.content import Paper

    pid = auth_client.post("/api/projects", json={"name": "Venue"}).json()["id"]
    db = SessionLocal()
    try:
        db.add(Paper(project_id=pid, title="V", source="ai_paper_finder", venue="CVPR", published="2026"))
        db.commit()
    finally:
        db.close()
    papers = auth_client.get(f"/api/projects/{pid}/discovery/papers").json()
    assert papers[0]["venue"] == "CVPR"
    assert papers[0]["published"] == "2026"
