"""ICLR26_Paper_Finder sidecar — semantic search over a multi-venue ChromaDB, plus
admin corpus management (add / delete / enable-disable a conference-year).

This service is the single owner of its Chroma corpus: it both READS (search) and
WRITES (ingest/delete) through one in-process PersistentClient, so there is never a
second writer and the in-memory HNSW index stays coherent — no stop/start dance.

Endpoints
  GET    /health
  GET    /venues   -> {"venues": [{"venue","year","count","enabled"}, ...]}
  GET    /search?q=<text>&limit=<n>&venue=<csv of "VENUE YEAR" | "VENUE" tokens>
  Admin (internal; the backend exposes these behind its require_admin guard):
  POST   /admin/ingest {venue,year,source:openreview|cvf} -> {task_id}; background ingest
  GET    /admin/ingest/{task_id}                          -> {state,count,message,...}
  DELETE /admin/conferences/{venue}/{year}                -> {deleted}
  POST   /admin/toggle {venue,year,enabled}               -> soft enable/disable for search
"""
from __future__ import annotations

import ast
import json
import logging
import os
import re
import subprocess
import threading
import uuid

import chromadb
from chromadb.utils import embedding_functions as ef
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import build_corpus
import collect_cvf
import collect_openreview

DB_PATH = os.environ.get("PAPERFINDER_DB_PATH", "/data/ICLR2026")
COLLECTION = os.environ.get("PAPERFINDER_COLLECTION", "MiniLM")
MODEL_NAME = os.environ.get("PAPERFINDER_MODEL", "all-MiniLM-L6-v2")
DEFAULT_VENUE = os.environ.get("PAPERFINDER_VENUE", "ICLR 2026")
VENUES_MANIFEST = os.environ.get(
    "PAPERFINDER_VENUES_FILE", os.path.join(os.path.dirname(DB_PATH) or "/data", "venues.json")
)
DISABLED_FILE = os.environ.get(
    "PAPERFINDER_DISABLED_FILE", os.path.join(os.path.dirname(DB_PATH) or "/data", "disabled_cohorts.json")
)

app = FastAPI(title="paperfinder", version="0.3.0")

_client = chromadb.PersistentClient(path=DB_PATH)
_embed_fn = ef.SentenceTransformerEmbeddingFunction(model_name=MODEL_NAME)

_AUTHOR_RE = re.compile(r"author\s*=\s*[{\"]([^}\"]*)", re.IGNORECASE)
_YEAR_RE = re.compile(r"year\s*=\s*[{\"]?\s*(\d{4})", re.IGNORECASE)
_YEAR_TOKEN = re.compile(r"(?:19|20)\d{2}")

logger = logging.getLogger("paperfinder")

# When a relevance threshold governs retrieval, this bounds the candidate pool (and
# thus the max papers a single threshold query can return) for safety/cost.
_THRESHOLD_MAX = 200

# Admin ingest task state (in-process — lost on restart, fine for an infrequent action).
# A single-flight lock guarantees exactly one writer to the corpus at a time;
# _DISABLED_LOCK guards the small enable/disable sidefile's read-modify-write.
_TASKS: dict[str, dict] = {}
_WRITE_LOCK = threading.Lock()
_DISABLED_LOCK = threading.Lock()


def _collection():
    return _client.get_collection(name=COLLECTION, embedding_function=_embed_fn)


def _parse_keywords(raw) -> list[str]:
    if isinstance(raw, list):
        return [str(k) for k in raw]
    if isinstance(raw, str) and raw.strip().startswith("["):
        try:
            return [str(k) for k in ast.literal_eval(raw)]
        except (ValueError, SyntaxError):
            pass
    return [raw] if raw else []


def _authors_from_bibtex(bibtex: str) -> list[str]:
    m = _AUTHOR_RE.search(bibtex or "")
    if not m:
        return []
    return [a.strip() for a in re.split(r"\s+and\s+", m.group(1)) if a.strip()]


def _published(meta: dict, bibtex: str) -> str:
    if meta.get("year"):
        return str(meta["year"])
    m = _YEAR_RE.search(bibtex or "")
    return m.group(1) if m else DEFAULT_VENUE


def _venue_manifest() -> list[dict]:
    try:
        with open(VENUES_MANIFEST) as fh:
            return json.load(fh).get("venues", [])
    except (OSError, ValueError):
        return []


def _disabled() -> set[tuple[str, str]]:
    """The admin-disabled (venue, year) cohorts (a small sidefile)."""
    try:
        with open(DISABLED_FILE) as fh:
            return {(str(c["venue"]), str(c["year"])) for c in json.load(fh).get("disabled", [])}
    except (OSError, ValueError, KeyError, TypeError):
        return set()


def _save_disabled(pairs: set[tuple[str, str]]) -> None:
    os.makedirs(os.path.dirname(DISABLED_FILE) or ".", exist_ok=True)
    with open(DISABLED_FILE, "w") as fh:
        json.dump({"disabled": [{"venue": v, "year": y} for v, y in sorted(pairs)]}, fh)


def _split_token(tok: str) -> tuple[str, str]:
    """'CVPR 2026' -> ('CVPR','2026'); 'ICLR' -> ('ICLR',''); keeps the venue intact for
    odd names by only treating a trailing 4-digit run as a year."""
    parts = tok.rsplit(" ", 1)
    if len(parts) == 2 and _YEAR_TOKEN.fullmatch(parts[1]):
        return parts[0].strip(), parts[1]
    return tok.strip(), ""


def _build_where(venue_param: str):
    """Turn the venue tokens into a Chroma where-clause over (venue, year) pairs, always
    dropping admin-disabled cohorts. Returns None (no filter), a where dict, or the
    sentinel "EMPTY" (the request resolved to zero allowed cohorts -> no results)."""
    disabled = _disabled()
    tokens = [t.strip() for t in (venue_param or "").split(",") if t.strip()]
    if not tokens:
        # No explicit selection: search everything except disabled cohorts.
        if not disabled:
            return None
        neg = [{"$or": [{"venue": {"$ne": v}}, {"year": {"$ne": y}}]} for v, y in sorted(disabled)]
        return neg[0] if len(neg) == 1 else {"$and": neg}
    manifest = _venue_manifest()
    pairs: set[tuple[str, str]] = set()
    venue_only: set[str] = set()  # bare venues the (stale/missing) manifest can't expand
    for tok in tokens:
        venue, year = _split_token(tok)
        if year:
            pairs.add((venue, year))
            continue
        years = [str(r.get("year", "") or "") for r in manifest if (r.get("venue", "") or "") == venue]
        if years:
            pairs.update((venue, y) for y in years)
        else:  # manifest unavailable for this venue -> match all its years rather than nothing
            logger.warning("bare venue %r has no manifest cohorts; searching all its years", venue)
            venue_only.add(venue)
    pairs -= disabled
    clauses = [{"$and": [{"venue": {"$eq": v}}, {"year": {"$eq": y}}]} for v, y in sorted(pairs)]
    clauses += [{"venue": {"$eq": v}} for v in sorted(venue_only)]
    if not clauses:
        return "EMPTY"
    return clauses[0] if len(clauses) == 1 else {"$or": clauses}


@app.on_event("startup")
def _refresh_manifest_on_start() -> None:
    """Rebuild the (venue, year) manifest from the live collection, so a corpus built by
    an older venue-only build_corpus (or edited out-of-band) is reported with per-year
    counts. Best-effort — never block startup on it."""
    try:
        build_corpus.refresh_manifest(_collection())
    except Exception as exc:  # noqa: BLE001
        logger.warning("startup manifest refresh skipped: %s", exc)


@app.get("/health")
def health() -> dict:
    try:
        return {
            "status": "ok",
            "db": DB_PATH,
            "collection": COLLECTION,
            "count": _collection().count(),
            "venues": _venue_manifest(),
        }
    except Exception as exc:  # noqa: BLE001
        return {"status": "degraded", "db": DB_PATH, "collection": COLLECTION, "error": str(exc)}


@app.get("/venues")
def venues() -> dict:
    disabled = _disabled()
    out = [
        {**row, "enabled": (str(row.get("venue", "")), str(row.get("year", ""))) not in disabled}
        for row in _venue_manifest()
    ]
    return {"venues": out}


@app.get("/search")
def search(q: str = "", limit: int = 50, venue: str = "", min_score: float = 0.0) -> dict:
    q = (q or "").strip()
    if not q:
        return {"results": []}
    threshold = max(0.0, min(float(min_score), 1.0))
    if threshold > 0:
        # Threshold + bound together: pull a wide candidate pool, keep everything scoring
        # above the bar, then return the `limit` highest-scoring of those (see the cap
        # after scoring). So the bar decides relevance and `limit` caps the count — you get
        # the best N papers above the threshold, not a fixed top-N regardless of score.
        total = _collection().count()
        n = min(_THRESHOLD_MAX, total) if total else 1
    else:
        n = max(1, min(int(limit), 100))
    where = _build_where(venue)
    if where == "EMPTY":  # all requested cohorts are disabled/unknown
        return {"results": []}

    try:
        res = _collection().query(query_texts=[q], n_results=n, where=where)
    except Exception as exc:  # noqa: BLE001
        # A query overlapping an in-flight admin upsert/delete can transiently error;
        # degrade to empty results rather than a 500 (the write is single-flighted + brief).
        logger.warning("search query failed (corpus write in progress?): %s", exc)
        return {"results": []}
    ids, docs = res["ids"][0], res["documents"][0]
    metas, dists = res["metadatas"][0], res["distances"][0]

    results = []
    for cid, doc, meta, dist in zip(ids, docs, metas, dists):
        meta = meta or {}
        bibtex = meta.get("_bibtex", "")
        score = round(1 - dist, 4) if dist is not None and dist <= 1 else (round(dist, 4) if dist is not None else 0.0)
        results.append(
            {
                "id": str(cid),
                "title": meta.get("title", "") or "",
                "abstract": (doc or "").strip(),
                "pdf_url": meta.get("pdf", "") or "",
                "categories": _parse_keywords(meta.get("keywords", "")),
                "authors": _authors_from_bibtex(bibtex),
                "published": _published(meta, bibtex),
                "venue": meta.get("venue", "") or "",
                "score": score,
            }
        )
    if threshold > 0:
        # Chroma returns by similarity desc, so the kept list is already highest-first;
        # cap it to the caller's `limit` to return the best N papers above the threshold.
        results = [r for r in results if r["score"] >= threshold][: max(1, int(limit))]
    return {"results": results}


# --------------------------------------------------------------------------- #
# Admin corpus management (called by the backend behind require_admin)
# --------------------------------------------------------------------------- #
class IngestIn(BaseModel):
    venue: str
    year: str
    source: str = "openreview"  # "openreview" (ICLR/ICML/NeurIPS) | "cvf" (CVPR/ICCV/WACV)


class ToggleIn(BaseModel):
    venue: str
    year: str
    enabled: bool


def _run_ingest(task_id: str, venue: str, year: str, source: str) -> None:
    task = _TASKS[task_id]
    try:
        task["state"] = "collecting"
        if source == "cvf":
            records = collect_cvf.collect(venue, year)
        else:
            records = collect_openreview.collect(venue, year, "Accepted")
        task["collected"] = len(records)
        if not records:
            task["state"] = "failed"
            task["message"] = f"No papers found for {venue} {year} ({source}). Not published yet?"
            return
        task["state"] = "embedding"
        col = _collection()
        n = build_corpus.embed_records(col, records, default_venue=venue, default_year=year)
        build_corpus.refresh_manifest(col)
        # A re-added cohort is implicitly re-enabled.
        d = _disabled()
        if (venue, year) in d:
            d.discard((venue, year))
            _save_disabled(d)
        task["count"] = n
        task["state"] = "done"
        task["message"] = f"Added {n} {venue} {year} papers."
    except Exception as exc:  # noqa: BLE001
        task["state"] = "failed"
        task["message"] = f"{type(exc).__name__}: {exc}"
    finally:
        _WRITE_LOCK.release()


@app.post("/admin/ingest")
def admin_ingest(body: IngestIn) -> dict:
    if not _WRITE_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="A corpus write is already in progress.")
    venue, year = body.venue.strip(), str(body.year).strip()
    if not venue or not year:
        _WRITE_LOCK.release()
        raise HTTPException(status_code=400, detail="venue and year are required.")
    # Bound the in-process task log: drop the oldest finished tasks (dicts keep insertion order).
    finished = [k for k, v in _TASKS.items() if v.get("state") in ("done", "failed")]
    while len(_TASKS) > 20 and finished:
        _TASKS.pop(finished.pop(0), None)
    task_id = uuid.uuid4().hex[:12]
    _TASKS[task_id] = {
        "state": "queued", "venue": venue, "year": year, "source": body.source,
        "collected": 0, "count": 0, "message": "",
    }
    try:
        threading.Thread(
            target=_run_ingest, args=(task_id, venue, year, body.source), daemon=True
        ).start()
    except Exception:  # noqa: BLE001 — never strand the lock if the thread fails to start
        _WRITE_LOCK.release()
        raise
    return {"task_id": task_id, "state": "queued"}


@app.get("/admin/ingest/{task_id}")
def admin_ingest_status(task_id: str) -> dict:
    task = _TASKS.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Unknown ingest task.")
    return {"task_id": task_id, **task}


@app.delete("/admin/conferences/{venue}/{year}")
def admin_delete_conference(venue: str, year: str) -> dict:
    if not _WRITE_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="A corpus write is already in progress.")
    try:
        deleted = build_corpus.delete_cohort(_collection(), venue, str(year))
        # Drop the now-deleted cohort from the disabled sidefile while still holding the
        # write lock, so a delete and a concurrent toggle can't clobber each other's write.
        with _DISABLED_LOCK:
            d = _disabled()
            if (venue, str(year)) in d:
                d.discard((venue, str(year)))
                _save_disabled(d)
    finally:
        _WRITE_LOCK.release()
    return {"venue": venue, "year": str(year), "deleted": deleted}


@app.post("/admin/toggle")
def admin_toggle(body: ToggleIn) -> dict:
    key = (body.venue, str(body.year))
    with _DISABLED_LOCK:
        d = _disabled()
        if body.enabled:
            d.discard(key)
        else:
            d.add(key)
        _save_disabled(d)
    return {"venue": body.venue, "year": str(body.year), "enabled": body.enabled}


@app.get("/admin/export")
def admin_export() -> StreamingResponse:
    """Stream a tar.gz of the corpus data dir so a server migration can carry the
    ChromaDB corpus the backend can't reach directly (its volume isn't backend-mounted).
    Called by the backend over the internal network. Read-only snapshot — avoid admin
    ingests during a migration so the corpus is consistent."""
    export_dir = os.path.dirname(DB_PATH) or "/data"
    proc = subprocess.Popen(["tar", "czf", "-", "-C", export_dir, "."], stdout=subprocess.PIPE)

    def _stream():
        try:
            while True:
                chunk = proc.stdout.read(1 << 16)
                if not chunk:
                    break
                yield chunk
        finally:
            if proc.stdout:
                proc.stdout.close()
            proc.wait()

    return StreamingResponse(
        _stream(),
        media_type="application/gzip",
        headers={"Content-Disposition": "attachment; filename=corpus.tgz"},
    )
