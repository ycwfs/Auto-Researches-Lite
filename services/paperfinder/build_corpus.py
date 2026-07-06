"""Embed a normalized papers JSONL into the sidecar's ChromaDB collection, tagged
with venue/year, and refresh the venues manifest. Idempotent (stable ids → upsert).

  python build_corpus.py --input papers.jsonl --venue NeurIPS --year 2024
  cat papers.jsonl | python build_corpus.py --venue NeurIPS --year 2024

Each input line is a JSON object:
  {"id"?, "title", "abstract", "pdf"?, "bibtex"?, "keywords"?, "venue"?, "year"?}
Per-paper venue/year win; the CLI --venue/--year fill any that are missing.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

import chromadb
from chromadb.utils import embedding_functions as ef

DB_PATH = os.environ.get("PAPERFINDER_DB_PATH", "/data/ICLR2026")
COLLECTION = os.environ.get("PAPERFINDER_COLLECTION", "MiniLM")
MODEL_NAME = os.environ.get("PAPERFINDER_MODEL", "all-MiniLM-L6-v2")
VENUES_MANIFEST = os.environ.get(
    "PAPERFINDER_VENUES_FILE", os.path.join(os.path.dirname(DB_PATH) or "/data", "venues.json")
)


def _stable_id(paper: dict, venue: str) -> str:
    base = paper.get("id") or paper.get("pdf") or f"{venue}|{paper.get('title', '')}"
    return hashlib.sha1(str(base).encode()).hexdigest()[:16]


def _read(input_path: str | None):
    stream = open(input_path) if input_path else sys.stdin
    with stream:
        for line in stream:
            line = line.strip()
            if line:
                yield json.loads(line)


# Cap how many ids one collection.get() binds: an unbounded get() fans metadata
# retrieval into a single `WHERE id IN (...)`, which Chroma's SQLite backend rejects
# with "too many SQL variables" once the corpus exceeds ~32k rows.
_GET_CHUNK = 5000


def refresh_manifest(collection) -> list[dict]:
    """Recount papers per (venue, year) and persist the manifest the sidecar serves.

    Year-granular so the picker and admin can show "CVPR 2026" vs "CVPR 2025"."""
    counts: dict[tuple[str, str], int] = {}
    all_ids = (collection.get(include=[]) or {}).get("ids") or []
    # Fetch metadata in id-chunks so no single query exceeds SQLite's bound-parameter
    # limit (the whole-corpus get() breaks once there are >~32k papers).
    for i in range(0, len(all_ids), _GET_CHUNK):
        got = collection.get(ids=all_ids[i : i + _GET_CHUNK], include=["metadatas"])
        for meta in got.get("metadatas") or []:
            v = (meta or {}).get("venue", "") or "Unknown"
            y = str((meta or {}).get("year", "") or "")
            counts[(v, y)] = counts.get((v, y), 0) + 1
    venues = [
        {"venue": v, "year": y, "count": c} for (v, y), c in sorted(counts.items())
    ]
    os.makedirs(os.path.dirname(VENUES_MANIFEST) or ".", exist_ok=True)
    with open(VENUES_MANIFEST, "w") as fh:
        json.dump({"venues": venues}, fh)
    return venues


def embed_records(collection, records, *, default_venue: str = "", default_year: str = "") -> int:
    """Upsert normalized paper records (dicts with title/abstract/venue/year/...) into the
    collection; records without an abstract are skipped (the embedder needs text). Stable
    sha1 ids mean re-ingesting a venue refreshes rather than duplicates. Returns the count."""
    ids, docs, metas = [], [], []
    for paper in records:
        abstract = (paper.get("abstract") or "").strip()
        if not abstract:
            continue
        venue = paper.get("venue") or default_venue
        year = str(paper.get("year") or default_year or "")
        kw = paper.get("keywords", "")
        ids.append(_stable_id(paper, venue))
        docs.append(abstract)
        metas.append(
            {
                "title": paper.get("title", "") or "",
                "keywords": kw if isinstance(kw, str) else json.dumps(kw),
                "pdf": paper.get("pdf", "") or "",
                "_bibtex": paper.get("bibtex", "") or "",
                "venue": venue,
                "year": year,
            }
        )
    for i in range(0, len(ids), 512):
        collection.upsert(ids=ids[i : i + 512], documents=docs[i : i + 512], metadatas=metas[i : i + 512])
    return len(ids)


def delete_cohort(collection, venue: str, year: str) -> int:
    """Remove every paper for a (venue, year) and refresh the manifest. Returns the count."""
    where = {"$and": [{"venue": {"$eq": venue}}, {"year": {"$eq": str(year)}}]}
    got = collection.get(where=where)
    ids = got.get("ids") or []
    if ids:
        collection.delete(ids=ids)
    refresh_manifest(collection)
    return len(ids)


def main() -> None:
    ap = argparse.ArgumentParser(description="Embed papers JSONL into the paperfinder ChromaDB.")
    ap.add_argument("--input", default=None, help="JSONL path (default: stdin)")
    ap.add_argument("--venue", default="", help="Fallback venue for papers missing one")
    ap.add_argument("--year", default="", help="Fallback year for papers missing one")
    args = ap.parse_args()

    client = chromadb.PersistentClient(path=DB_PATH)
    embed_fn = ef.SentenceTransformerEmbeddingFunction(model_name=MODEL_NAME)
    col = client.get_or_create_collection(name=COLLECTION, embedding_function=embed_fn)

    n = embed_records(col, _read(args.input), default_venue=args.venue, default_year=args.year)
    venues = refresh_manifest(col)
    print(f"Upserted {n} papers; collection now {col.count()}; venues: {venues}")


if __name__ == "__main__":
    main()
