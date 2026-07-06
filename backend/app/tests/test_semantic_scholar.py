"""Semantic Scholar query construction: real query text + recency + field filter.

The HTTP call is monkeypatched, so these assert the request params we build, not
live results.
"""
from __future__ import annotations

from app.integrations.sources.base import SourceQuery
from app.integrations.sources.semantic_scholar import SemanticScholarSource


class _FakeResp:
    status_code = 200

    def json(self) -> dict:
        return {"data": []}


def _capture_params(monkeypatch) -> dict:
    captured: dict = {}

    def _fake_request(method, url, **kwargs):
        captured["params"] = kwargs.get("params", {})
        return _FakeResp()

    monkeypatch.setattr(
        "app.integrations.sources.semantic_scholar.request_with_retry", _fake_request
    )
    return captured


def test_topic_used_when_keywords_absent(monkeypatch) -> None:
    monkeypatch.delenv("S2_FIELDS_OF_STUDY", raising=False)
    monkeypatch.setenv("S2_RECENCY_DAYS", "365")
    captured = _capture_params(monkeypatch)

    SemanticScholarSource().fetch(
        SourceQuery(categories=["cs.AI", "cs.LG"], keywords=[], config={"topic": "mamba"})
    )
    p = captured["params"]

    assert p["query"] == "mamba"  # topic, never the raw "cs.AI cs.LG" codes
    assert p["publicationDateOrYear"].endswith(":")  # recency lower-bound set
    assert p["fieldsOfStudy"] == "Computer Science"  # derived from cs.* categories


def test_combines_keywords_and_topic(monkeypatch) -> None:
    captured = _capture_params(monkeypatch)
    SemanticScholarSource().fetch(
        SourceQuery(categories=["cs.AI"], keywords=["state space models"], config={"topic": "mamba"})
    )
    assert captured["params"]["query"] == "state space models mamba"


def test_recency_can_be_disabled(monkeypatch) -> None:
    monkeypatch.setenv("S2_RECENCY_DAYS", "0")
    captured = _capture_params(monkeypatch)
    SemanticScholarSource().fetch(SourceQuery(categories=["cs.AI"], config={"topic": "x"}))
    assert "publicationDateOrYear" not in captured["params"]


def test_per_user_config_overrides_env(monkeypatch) -> None:
    monkeypatch.setenv("S2_RECENCY_DAYS", "365")  # env wants a recency window
    captured = _capture_params(monkeypatch)
    SemanticScholarSource().fetch(
        SourceQuery(
            categories=["cs.AI"],
            config={
                "topic": "x",
                "s2_recency_days": 0,  # per-user disables it -> overrides env
                "s2_min_citations": 7,
                "s2_fields_of_study": "off",  # per-user disables the field filter
            },
        )
    )
    p = captured["params"]
    assert "publicationDateOrYear" not in p  # user's 0 beat env's 365
    assert p["minCitationCount"] == 7
    assert "fieldsOfStudy" not in p  # "off" disables
