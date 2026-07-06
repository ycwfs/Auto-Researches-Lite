"""Semantic-first relevance retrieval (replaces the substring keyword filter)."""
from __future__ import annotations


def test_rank_relevant_uses_embeddings_when_available(monkeypatch) -> None:
    from app.services import retrieval

    papers = [{"t": "irrelevant"}, {"t": "relevant"}]

    # Vectors for [query, paper0, paper1]: query is close to paper1, orthogonal to paper0.
    def fake_embed(texts, db=None):
        return [[1.0, 0.0], [0.0, 1.0], [1.0, 0.2]]

    monkeypatch.setattr(retrieval.embeddings, "embed_texts", fake_embed)
    ranked, method = retrieval.rank_relevant(
        "query", papers, text_of=lambda p: p["t"], top_k=2, db=None
    )
    assert method == "embeddings"
    assert ranked[0]["t"] == "relevant"  # ranked by cosine, not text overlap


def test_rank_relevant_falls_back_to_tfidf(monkeypatch) -> None:
    from app.services import retrieval

    monkeypatch.setattr(retrieval.embeddings, "embed_texts", lambda texts, db=None: None)
    papers = [
        {"t": "A study of medieval poetry and rhyme schemes"},
        {"t": "Deep reinforcement learning for robotic control"},
    ]
    ranked, method = retrieval.rank_relevant(
        "reinforcement learning robotics", papers, text_of=lambda p: p["t"], top_k=2
    )
    assert method == "tfidf"
    assert "reinforcement" in ranked[0]["t"].lower()
    # The medieval-poetry paper shares no terms (score 0) → dropped, not padded in.
    assert len(ranked) == 1


def test_rank_relevant_empty_query_returns_capped(monkeypatch) -> None:
    from app.services import retrieval

    monkeypatch.setattr(retrieval.embeddings, "embed_texts", lambda texts, db=None: None)
    papers = [{"t": "a"}, {"t": "b"}, {"t": "c"}]
    ranked, method = retrieval.rank_relevant("   ", papers, text_of=lambda p: p["t"], top_k=2)
    assert method == "none"
    assert len(ranked) == 2
