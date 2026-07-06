"""Seed a TINY multi-venue demo ChromaDB so the sidecar can be exercised WITHOUT
the ~1.5GB bundle. For local testing/CI only — real deploys use download_data.sh
or add_venue.sh.

  docker compose run --rm paperfinder python seed_demo_db.py
"""
from __future__ import annotations

import os

import chromadb
from chromadb.utils import embedding_functions as ef

from build_corpus import refresh_manifest

DB_PATH = os.environ.get("PAPERFINDER_DB_PATH", "/data/ICLR2026")
COLLECTION = os.environ.get("PAPERFINDER_COLLECTION", "MiniLM")

# Distinct venues so the ?venue= filter is exercised.
_PAPERS = [
    {
        "title": "Mamba: Linear-Time Sequence Modeling with Selective State Spaces",
        "abstract": "We introduce Mamba, a selective state space model achieving linear-time "
        "sequence modeling that matches Transformers on language with faster inference.",
        "keywords": "['state space models', 'sequence modeling', 'efficient attention']",
        "pdf": "https://openreview.net/pdf?id=demo-mamba",
        "_bibtex": "@inproceedings{gu2024mamba, title={Mamba}, author={Albert Gu and Tri Dao}, year={2024}}",
        "venue": "ICLR",
        "year": "2024",
    },
    {
        "title": "Vision Mamba: Efficient Visual Representation Learning with Bidirectional SSMs",
        "abstract": "Vision Mamba applies bidirectional state space models to image patches for "
        "efficient visual representation learning, rivaling vision transformers with lower memory.",
        "keywords": "['vision', 'state space models', 'image classification']",
        "pdf": "https://openreview.net/pdf?id=demo-vim",
        "_bibtex": "@inproceedings{zhu2024vim, title={Vision Mamba}, author={Lianghui Zhu and others}, year={2024}}",
        "venue": "CVPR",
        "year": "2024",
    },
    {
        "title": "Diffusion Models Beat GANs on Image Synthesis",
        "abstract": "We show diffusion models can achieve superior image sample quality to GANs "
        "through classifier guidance and improved architectures.",
        "keywords": "['diffusion models', 'generative models', 'image synthesis']",
        "pdf": "https://openreview.net/pdf?id=demo-diff",
        "_bibtex": "@inproceedings{dhariwal2021diff, title={Diffusion Beats GANs}, author={Prafulla Dhariwal and Alex Nichol}, year={2021}}",
        "venue": "NeurIPS",
        "year": "2021",
    },
]


def main() -> None:
    client = chromadb.PersistentClient(path=DB_PATH)
    embed_fn = ef.SentenceTransformerEmbeddingFunction(model_name="all-MiniLM-L6-v2")
    try:
        client.delete_collection(COLLECTION)
    except Exception:  # noqa: BLE001 — fresh DB has no collection yet
        pass
    col = client.get_or_create_collection(name=COLLECTION, embedding_function=embed_fn)
    col.add(
        ids=[f"demo-{i}" for i in range(len(_PAPERS))],
        documents=[p["abstract"] for p in _PAPERS],
        metadatas=[
            {
                "title": p["title"],
                "keywords": p["keywords"],
                "pdf": p["pdf"],
                "_bibtex": p["_bibtex"],
                "venue": p["venue"],
                "year": p["year"],
            }
            for p in _PAPERS
        ],
    )
    venues = refresh_manifest(col)
    print(f"Seeded {col.count()} demo papers into '{COLLECTION}' at {DB_PATH}; venues: {venues}")


if __name__ == "__main__":
    main()
