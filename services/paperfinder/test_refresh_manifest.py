"""Regression: refresh_manifest must read the corpus metadata in id-chunks.

An unbounded collection.get(include=["metadatas"]) fans out to a single
`WHERE id IN (...all ids...)`, which ChromaDB's SQLite backend rejects with
"too many SQL variables" once the corpus grows past ~32k papers (observed after
adding CVPR 2024, total 36,595). Chunking keeps every query under the limit.

Run inside the sidecar env (chromadb installed):  pytest services/paperfinder
"""
from __future__ import annotations

import chromadb

import build_corpus


def test_refresh_manifest_counts_across_chunks(monkeypatch, tmp_path) -> None:
    # Force several chunks with only a handful of papers so the loop's
    # chunk-boundary counting is exercised without a 32k-row corpus.
    monkeypatch.setattr(build_corpus, "_GET_CHUNK", 3)
    monkeypatch.setattr(build_corpus, "VENUES_MANIFEST", str(tmp_path / "venues.json"))

    client = chromadb.PersistentClient(path=str(tmp_path / "db"))
    col = client.create_collection("papers", embedding_function=None)
    cohorts = {("CVPR", "2024"): 4, ("ICLR", "2026"): 3}  # 7 papers → 3 id-chunks
    ids, embs, metas = [], [], []
    for (venue, year), n in cohorts.items():
        for i in range(n):
            ids.append(f"{venue}{year}{i}")
            embs.append([0.1, 0.2, 0.3])
            metas.append({"venue": venue, "year": year, "title": f"t{i}"})
    col.upsert(ids=ids, embeddings=embs, metadatas=metas)

    venues = build_corpus.refresh_manifest(col)  # must not raise, must count all
    counts = {(v["venue"], v["year"]): v["count"] for v in venues}
    assert counts == {("CVPR", "2024"): 4, ("ICLR", "2026"): 3}
