"""ai_paper_finder source: explicit verbatim query, topic fallback, endpoint, isolation."""
from __future__ import annotations

import requests

from app.integrations.sources.ai_paper_finder import AiPaperFinderSource
from app.integrations.sources.base import SourceQuery


class _Resp:
    def __init__(self, status_code: int = 200, payload=None) -> None:
        self.status_code = status_code
        self._payload = {"results": []} if payload is None else payload

    def json(self):
        return self._payload


def _patch(monkeypatch, *, resp=None, raise_exc=None) -> dict:
    captured: dict = {}

    def _fake(method, url, **kwargs):
        captured["url"] = url
        captured["params"] = kwargs.get("params", {})
        if raise_exc:
            raise raise_exc
        return resp or _Resp()

    monkeypatch.setattr("app.integrations.sources.ai_paper_finder.request_with_retry", _fake)
    return captured


def test_uses_topic_when_keywords_absent(monkeypatch) -> None:
    cap = _patch(monkeypatch)
    AiPaperFinderSource().fetch(
        SourceQuery(
            categories=["cs.AI", "cs.LG"],
            keywords=[],
            config={"endpoint": "http://pf/search", "topic": "mamba"},
        )
    )
    assert cap["params"]["q"] == "mamba"  # topic, not the raw "cs.AI cs.LG" codes


def test_combines_keywords_and_topic(monkeypatch) -> None:
    cap = _patch(monkeypatch)
    AiPaperFinderSource().fetch(
        SourceQuery(keywords=["state space models"], config={"endpoint": "http://pf/search", "topic": "mamba"})
    )
    assert cap["params"]["q"] == "state space models mamba"


def test_explicit_query_used_verbatim(monkeypatch) -> None:
    cap = _patch(monkeypatch)
    abstract = "We present a linear-time sequence model with selective state spaces."
    AiPaperFinderSource().fetch(
        SourceQuery(
            keywords=["state space models"],  # ignored in favour of the explicit query
            config={"endpoint": "http://pf/search", "topic": "mamba", "paper_finder_query": abstract},
        )
    )
    assert cap["params"]["q"] == abstract  # verbatim, not "state space models mamba"


def test_blank_explicit_query_falls_back_to_keywords_topic(monkeypatch) -> None:
    cap = _patch(monkeypatch)
    AiPaperFinderSource().fetch(
        SourceQuery(
            keywords=["state space models"],
            config={"endpoint": "http://pf/search", "topic": "mamba", "paper_finder_query": "   "},
        )
    )
    assert cap["params"]["q"] == "state space models mamba"  # whitespace-only -> fallback


def test_endpoint_falls_back_to_env(monkeypatch) -> None:
    monkeypatch.setenv("PAPERFINDER_ENDPOINT", "http://env-pf/search")
    cap = _patch(monkeypatch)
    AiPaperFinderSource().fetch(SourceQuery(keywords=["x"], config={}))
    assert cap["url"] == "http://env-pf/search"


def test_no_endpoint_is_graceful_noop(monkeypatch) -> None:
    monkeypatch.delenv("PAPERFINDER_ENDPOINT", raising=False)
    cap = _patch(monkeypatch)
    assert AiPaperFinderSource().fetch(SourceQuery(keywords=["x"], config={})) == []
    assert "url" not in cap  # never hit the network


def test_venue_filter_passed_when_set(monkeypatch) -> None:
    cap = _patch(monkeypatch)
    AiPaperFinderSource().fetch(
        SourceQuery(keywords=["x"], config={"endpoint": "http://pf/search", "venues": ["ICLR", "NeurIPS"]})
    )
    assert cap["params"]["venue"] == "ICLR,NeurIPS"


def test_no_venue_param_when_empty(monkeypatch) -> None:
    cap = _patch(monkeypatch)
    AiPaperFinderSource().fetch(
        SourceQuery(keywords=["x"], config={"endpoint": "http://pf/search", "venues": []})
    )
    assert "venue" not in cap["params"]


def test_min_score_param_passed_when_set(monkeypatch) -> None:
    cap = _patch(monkeypatch)
    AiPaperFinderSource().fetch(
        SourceQuery(keywords=["x"], config={"endpoint": "http://pf/search", "paper_finder_min_score": 0.6})
    )
    assert cap["params"]["min_score"] == 0.6


def test_no_min_score_param_when_zero_or_absent(monkeypatch) -> None:
    cap = _patch(monkeypatch)
    AiPaperFinderSource().fetch(
        SourceQuery(keywords=["x"], config={"endpoint": "http://pf/search", "paper_finder_min_score": 0})
    )
    assert "min_score" not in cap["params"]
    cap = _patch(monkeypatch)
    AiPaperFinderSource().fetch(SourceQuery(keywords=["x"], config={"endpoint": "http://pf/search"}))
    assert "min_score" not in cap["params"]


def test_maps_results_and_isolates_errors(monkeypatch) -> None:
    payload = {"results": [{"title": "Mamba", "abstract": "ssm", "pdf_url": "http://x/p.pdf", "id": "or:1"}]}
    _patch(monkeypatch, resp=_Resp(200, payload))
    out = AiPaperFinderSource().fetch(
        SourceQuery(keywords=["mamba"], config={"endpoint": "http://pf/search"})
    )
    assert out and out[0]["title"] == "Mamba" and out[0]["source"] == "ai_paper_finder"

    _patch(monkeypatch, raise_exc=requests.ConnectionError("boom"))
    assert AiPaperFinderSource().fetch(
        SourceQuery(keywords=["mamba"], config={"endpoint": "http://pf/search"})
    ) == []


def test_maps_semantic_score_into_finder_score(monkeypatch) -> None:
    """The sidecar's per-paper cosine `score` is carried through as `finder_score`."""
    payload = {"results": [
        {"title": "A", "id": "or:1", "score": 0.87},
        {"title": "B", "id": "or:2"},          # no score -> 0.0
        {"title": "C", "id": "or:3", "score": "oops"},  # invalid -> 0.0
    ]}
    _patch(monkeypatch, resp=_Resp(200, payload))
    out = AiPaperFinderSource().fetch(
        SourceQuery(keywords=["x"], config={"endpoint": "http://pf/search"})
    )
    assert [round(p["finder_score"], 2) for p in out] == [0.87, 0.0, 0.0]
