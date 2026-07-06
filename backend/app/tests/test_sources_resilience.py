"""Paper-source resilience: bounded arXiv fetch + per-source failure isolation."""
from __future__ import annotations

import socket

from app.integrations.sources import SOURCE_REGISTRY, fetch_from_sources
from app.integrations.sources.arxiv import ArxivSource
from app.integrations.sources.base import SourceQuery


def test_arxiv_source_sets_and_restores_socket_timeout(monkeypatch) -> None:
    monkeypatch.setenv("ARXIV_HTTP_TIMEOUT", "7")
    socket.setdefaulttimeout(None)  # known starting point
    seen: dict[str, float | None] = {}

    def _fake_fetch(config, days_back=3):
        seen["during"] = socket.getdefaulttimeout()
        return []

    monkeypatch.setattr("app.integrations.sources.arxiv.fetch_arxiv_papers", _fake_fetch)

    ArxivSource().fetch(SourceQuery(categories=["cs.LG"], max_results=3))

    assert seen["during"] == 7.0  # timeout pinned during the fetch
    assert socket.getdefaulttimeout() is None  # and restored afterward


def test_per_source_max_results_override(monkeypatch) -> None:
    seen: dict[str, int] = {}

    def _recorder(key):
        def _fetch(query):
            seen[key] = query.max_results
            return []

        return _fetch

    monkeypatch.setattr(SOURCE_REGISTRY["arxiv"], "fetch", _recorder("arxiv"))
    monkeypatch.setattr(SOURCE_REGISTRY["semantic_scholar"], "fetch", _recorder("semantic_scholar"))

    fetch_from_sources(
        ["arxiv", "semantic_scholar"],
        SourceQuery(max_results=20),
        max_results_by_source={"semantic_scholar": 7},
    )

    assert seen["arxiv"] == 20  # no override -> falls back to query.max_results
    assert seen["semantic_scholar"] == 7  # per-source target applied


def test_failing_source_does_not_break_discovery(monkeypatch) -> None:
    def _boom(_query):
        raise RuntimeError("arXiv unreachable")

    def _ok(_query):
        return [{"id": "2401.00001", "title": "Working paper", "source": "semantic_scholar"}]

    monkeypatch.setattr(SOURCE_REGISTRY["arxiv"], "fetch", _boom)
    monkeypatch.setattr(SOURCE_REGISTRY["semantic_scholar"], "fetch", _ok)

    status: list[dict] = []
    papers = fetch_from_sources(
        ["arxiv", "semantic_scholar"], SourceQuery(), status_out=status
    )

    # The dead source is isolated; the working one still contributes.
    assert [p["id"] for p in papers] == ["2401.00001"]
    by_source = {s["source"]: s for s in status}
    assert by_source["arxiv"]["status"] == "error"
    assert "arXiv unreachable" in by_source["arxiv"]["reason"]
    assert by_source["semantic_scholar"]["status"] == "ok"
    assert by_source["semantic_scholar"]["count"] == 1
