"""Stage 1: paper discovery orchestration (fetch -> summarize -> trends).

Papers ACCUMULATE across runs (deduped by arXiv id / title) so the reference set
grows over time. Discovery fetches from the project's enabled sources.
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import engine
from app.core.paths import discovery_dir
from app.integrations.auto_research import analyze_trends
from app.integrations.sources import fetch_from_sources
from app.integrations.sources.base import SourceQuery, norm_title
from app.models.admin import PaperSource
from app.models.content import Paper
from app.models.enums import JobStatus, JobType
from app.models.job import Job
from app.models.project import Project
from app.services import paper_db
from app.services.job_control import JobCanceled, mark_canceled, raise_if_canceled
# Re-exported for backward compatibility with callers that import from this module.
from app.services.model_select import build_llm_for_step, build_llm_for_user  # noqa: F401

logger = logging.getLogger("far.discovery")


def _dedup_key(arxiv_id: str | None, title: str | None) -> str:
    """Per-project dedup identity: the arxiv id with any version suffix stripped
    (so 2606.06338 and 2606.06338v1 match), else the normalized title."""
    a = re.sub(r"v\d+$", "", (arxiv_id or "").strip().lower())
    return a or " ".join((title or "").lower().split())


# Namespace for the per-project advisory lock ("DISC"). A regular discovery and an AI
# Paper Finder run now have different JobTypes, so they can run in parallel worker
# processes; this lock serializes their per-project paper-write section so they can't
# race the dedup and double-store a paper present in two sources. The key is a single
# namespaced bigint (high 32 bits = namespace, low 32 = project id) to use the
# unambiguous pg_advisory_lock(bigint) overload.
_LOCK_NS = 0x44495343


def _lock_key(project_id: int) -> int:
    return (_LOCK_NS << 32) | (project_id & 0xFFFFFFFF)


def _acquire_write_lock(project_id: int):
    """Take the per-project write lock on a DEDICATED connection (a pooled session lock
    would leak across the loop's periodic commits). Returns the connection to release
    later, or None on non-Postgres (SQLite serializes writes itself)."""
    if not engine.dialect.name.startswith("postgresql"):
        return None
    conn = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": _lock_key(project_id)})
    return conn


def _release_write_lock(conn, project_id: int) -> None:
    if conn is None:
        return
    try:
        conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _lock_key(project_id)})
    except Exception:  # noqa: BLE001 — releasing a never-held lock is harmless
        pass
    finally:
        conn.close()


# Marker present in the offline bilingual summary (llm._mock_summary summary_en).
_MOCK_SUMMARY_MARK = "[offline summary"


def _heal_stale_summaries(db: Session, project: Project, llm, steer: str) -> int:
    """Re-summarize project papers whose bilingual summary is stuck on the offline mock
    (a real LLM is now available). Best-effort; returns how many were healed."""
    stale = (
        db.query(Paper)
        .filter(Paper.project_id == project.id, Paper.summary_en.like(f"%{_MOCK_SUMMARY_MARK}%"))
        .all()
    )
    healed = 0
    for pap in stale:
        try:
            s = llm.summarize_paper(
                {"title": pap.title, "abstract": pap.abstract},
                project.keywords or [],
                context=steer,
            )
        except Exception:  # noqa: BLE001 — best-effort
            continue
        if _MOCK_SUMMARY_MARK not in (s.get("summary_en") or ""):  # only update when real now
            pap.summary_en = s.get("summary_en", "")
            pap.summary_zh = s.get("summary_zh", "")
            pap.relevance = s.get("relevance", pap.relevance)
            healed += 1
    if healed:
        db.commit()
    return healed


def _enabled_source_keys(
    db: Session, requested: list[str]
) -> tuple[list[str], dict[str, dict], list[str]]:
    """Intersect requested sources with admin-enabled ones.

    Returns ``(enabled_keys, configs, skipped)`` where ``skipped`` lists requested
    sources the admin has NOT enabled. The caller surfaces ``skipped`` in the job log
    so a dropped selection never silently masquerades as an arXiv run. When no
    PaperSource rows exist yet (un-seeded), all requested sources pass.
    """
    from app.api.routes.admin import decode_source_keys

    requested = requested or ["arxiv"]
    rows = db.query(PaperSource).all()
    if not rows:
        return requested, {}, []
    enabled = {r.key for r in rows if r.enabled}
    configs: dict[str, dict] = {}
    for r in rows:
        cfg = dict(r.config or {})
        # Inject the decrypted key pool transiently (never persisted plaintext) so the
        # source can rotate across keys to dodge per-key rate limits.
        pool = decode_source_keys(r.api_keys_enc or "")
        if pool:
            cfg["api_keys"] = pool
        configs[r.key] = cfg
    keys = [k for k in requested if k in enabled]
    skipped = [k for k in requested if k not in enabled]
    return keys, configs, skipped


def run_discovery(db: Session, job_id: int) -> None:
    """Execute a discovery job. Updates the Job row throughout."""
    job = db.get(Job, job_id)
    if job is None:
        return
    project = db.get(Project, job.project_id)
    if project is None:
        _fail(db, job, "Project not found")
        return

    lock_conn = None
    try:
        raise_if_canceled(db, job)  # canceled before the worker picked it up — don't resurrect it
        out_dir = discovery_dir(project.owner_id, project.id)
        # AI Paper Finder is decoupled from regular/scheduled discovery (its corpus is
        # fixed): a JobType.paper_finder run fetches only it, while a regular run (and
        # every scheduled run, which is JobType.discovery) drops it from the sources.
        is_paper_finder = job.type == JobType.paper_finder
        if is_paper_finder:
            requested = ["ai_paper_finder"]
        else:
            requested = [s for s in (project.paper_sources or ["arxiv"]) if s != "ai_paper_finder"]
        if not requested:
            _set(
                db, job, status=JobStatus.succeeded, progress=100,
                log="No live paper sources enabled — the AI Paper Finder runs from its own button.",
            )
            return
        keys, source_configs, skipped = _enabled_source_keys(db, requested)
        notes: list[str] = []
        if skipped:
            notes.append(
                f"selected source(s) not enabled by the admin, skipped: {', '.join(skipped)}"
            )
        if not keys:
            if is_paper_finder:
                # The AI Paper Finder run whose source the admin disabled — don't
                # substitute arXiv; succeed with a clear note.
                _set(
                    db, job, status=JobStatus.succeeded, progress=100,
                    log=f"Requested source(s) not enabled by the admin: {', '.join(requested)}.",
                )
                return
            # The user's whole selection is disabled — be explicit instead of silently
            # substituting arXiv (which previously looked like the chosen source failed).
            keys = ["arxiv"]
            notes.append(
                "none of your selected sources are enabled — falling back to arXiv "
                "(enable the source in the admin panel)"
            )
        log = f"Fetching papers from: {', '.join(keys)} ..."
        if notes:
            log += "\n" + "\n".join(f"⚠ {n}" for n in notes)
        _set(db, job, status=JobStatus.running, progress=5, log=log)

        query = SourceQuery(
            categories=project.categories or ["cs.AI"],
            keywords=project.keywords or [],
            max_results=project.max_results or 20,
            days_back=5,
            # `topic` lets keyword-less projects still issue a meaningful query to
            # sources that search by free text (e.g. Semantic Scholar) instead of
            # falling back to arXiv category codes. The s2_* keys carry this
            # project's Semantic Scholar tuning (None falls back to the env default).
            config={
                "papers_dir": str(out_dir / "papers"),
                "topic": project.name or "",
                "s2_recency_days": project.s2_recency_days,
                "s2_fields_of_study": project.s2_fields_of_study,
                "s2_min_citations": project.s2_min_citations,
                "venues": project.paper_finder_venues or [],
                # AI Paper Finder's explicit semantic query (verbatim; ideally an abstract).
                # Empty -> the source falls back to its keywords+name composition.
                "paper_finder_query": project.paper_finder_query or "",
                # AI Paper Finder relevance threshold (0..1). >0 governs retrieval: the
                # source returns every paper scoring >= this, not a fixed top-N.
                "paper_finder_min_score": project.paper_finder_min_score or 0.0,
            },
        )
        source_status: list[dict] = []
        fetched = fetch_from_sources(
            keys, query, source_configs, status_out=source_status,
            max_results_by_source=project.source_max_results or {},
        )
        status_line = "; ".join(
            f"{s['source']}: {s.get('count', s.get('reason', s['status']))}"
            for s in source_status
        )
        _append(db, job, f"Source results — {status_line}", progress=25)
        _append(db, job, f"Fetched {len(fetched)} paper(s) across sources.", progress=30)

        llm = build_llm_for_step(db, job.user_id, project, "summary")
        # Steer summarization + relevance scoring by the project's accumulated context
        # (focus + ideas so far). Empty on the very first run.
        from app.services import context_service

        steer = context_service.build_steering_context(db, project)
        _append(
            db, job,
            f"Summarizing new papers with {'offline mock' if llm.offline else llm.config.provider}"
            + (" (context-steered)" if steer else "")
            + "...",
            progress=40,
        )

        # Self-heal: a paper summarized during an earlier offline/failed run keeps a mock
        # bilingual summary forever (unlike the self-upgrading 5-point). Now that a real
        # LLM is configured, re-summarize those stale papers.
        if not llm.offline:
            healed = _heal_stale_summaries(db, project, llm, steer)
            if healed:
                _append(db, job, f"Re-summarized {healed} paper(s) that were stuck on an offline summary.")

        # Serialize the per-project dedup+insert against a concurrent run of the other
        # JobType (regular discovery vs. AI Paper Finder). Uncontended (different projects
        # or no overlap) this is instant; only a same-project overlap waits.
        lock_conn = _acquire_write_lock(project.id)
        project_papers = db.query(Paper).filter(Paper.project_id == project.id).all()
        existing = {_dedup_key(p.arxiv_id, p.title) for p in project_papers}
        # Cross-source / cross-run title dedup: the same paper from AI Paper Finder
        # (content-hash id) and arXiv (arXiv id) has different ids, so id-only dedup
        # would store it twice. Map title -> the stored Paper so a later venue-bearing
        # copy can ENRICH the existing row with its conference (e.g. add "CVPR" to a
        # paper first found on arXiv) instead of being lost.
        existing_titles = {norm_title(p.title): p for p in project_papers if p.title}
        # Cap total accumulated papers per project to keep the DB bounded.
        cap = project.max_total_papers if project.max_total_papers is not None else 600
        new_count = 0
        capped = False
        new_papers: list[Paper] = []
        for i, p in enumerate(fetched):
            raise_if_canceled(db, job)
            if len(existing) >= cap:
                capped = True
                break  # project reached its total-papers cap
            ident = _dedup_key(str(p.get("id", "")), p.get("title", ""))
            tkey = norm_title(p.get("title", ""))
            if not ident or ident in existing:
                continue  # already stored under the same id
            title_match = existing_titles.get(tkey) if tkey else None
            if title_match is not None:
                # A paper with this title is already stored. Carry over the conference
                # if the stored row lacks one. Only DROP the fetched paper when it has no
                # real arXiv id — a paper with its own arXiv id is treated as distinct so
                # two genuinely different same-title arXiv papers are never silently lost.
                if p.get("venue") and not (title_match.venue or ""):
                    title_match.venue = str(p.get("venue", "") or "")[:80]
                    if p.get("published") and not title_match.published:
                        title_match.published = str(p.get("published", ""))[:40]
                # Carry over the AI Paper Finder cosine if this copy has one and the stored
                # row doesn't (e.g. first found on arXiv, later matched by the finder).
                fs = float(p.get("finder_score") or 0.0)
                if fs and not (title_match.finder_score or 0.0):
                    title_match.finder_score = fs
                if not paper_db._arxiv_id(p):
                    continue
            existing.add(ident)
            summary = llm.summarize_paper(p, project.keywords or [], context=steer)
            paper = Paper(
                project_id=project.id,
                arxiv_id=str(p.get("id", "")),
                source=str(p.get("source", "")),
                title=(p.get("title", "") or "").strip(),
                authors=p.get("authors", []),
                abstract=p.get("abstract", ""),
                categories=p.get("categories", []),
                pdf_url=p.get("pdf_url", ""),
                published=p.get("published", ""),
                venue=str(p.get("venue", "") or "")[:80],
                summary_en=summary["summary_en"],
                summary_zh=summary["summary_zh"],
                relevance=summary["relevance"],
                # AI Paper Finder cosine similarity (0 for other sources).
                finder_score=float(p.get("finder_score") or 0.0),
            )
            db.add(paper)
            if tkey:
                existing_titles[tkey] = paper  # so the next same-title paper enriches/dedups
            new_papers.append(paper)
            new_count += 1
            if fetched and i % 5 == 0:
                _set(db, job, progress=40 + int(30 * (i + 1) / max(1, len(fetched))))
        db.flush()
        # The per-project dedup+insert is done and committed; release the write lock now
        # so the slower global paper-doc build + trends overlap with a concurrent run of
        # the other JobType (its insert loop will see these just-committed papers).
        _release_write_lock(lock_conn, project.id)
        lock_conn = None
        if capped:
            _append(db, job, f"Reached the project cap of {cap} papers; stopped adding more.")

        # Convert + 5-point-summarize each NEW paper into the global paper database
        # (dedup; reused across projects + by idea generation). Best-effort and
        # time-budgeted — the most relevant are done first; the rest fall back to the
        # quick summary and get the 5-point lazily at idea time.
        _build_paper_documents(db, job, project, llm, new_papers)

        # Trends over the full accumulated paper set for a fuller picture.
        all_papers = db.query(Paper).filter(Paper.project_id == project.id).all()
        total = len(all_papers)
        paper_dicts = [
            {"title": p.title, "abstract": p.abstract, "categories": p.categories or []}
            for p in all_papers
        ]
        _append(db, job, f"Analyzing trends over {total} papers...", progress=80)
        trends = analyze_trends(paper_dicts, out_dir / "wordcloud.png")
        (out_dir / "trends.json").write_text(json.dumps(trends, ensure_ascii=False, indent=2))

        (out_dir / "summary.json").write_text(
            json.dumps(
                {
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "new_papers": new_count,
                    "total_papers": total,
                    "sources": keys,
                    "offline": llm.offline,
                    "trends": trends,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        raise_if_canceled(db, job)  # a cancel arriving after the last loop checkpoint still wins
        _update_context(db, project)
        _set(
            db, job, status=JobStatus.succeeded, progress=100,
            log=f"Done: +{new_count} new papers ({total} total).",
        )
    except JobCanceled:
        mark_canceled(db, job, "Canceled by user — discovery stopped.")
    except Exception as exc:  # noqa: BLE001
        logger.exception("discovery job %s failed", job_id)
        _fail(db, job, f"{type(exc).__name__}: {exc}")
    finally:
        _release_write_lock(lock_conn, project.id)


def _build_paper_documents(
    db: Session, job: Job, project: Project, llm, new_papers: list[Paper]
) -> None:
    """Convert + 5-point-summarize each new paper into the global paper DB (dedup),
    link it to the project, and record paper.document_id. Best-effort and
    time-budgeted, most-relevant first; the rest keep the quick summary and get the
    5-point lazily at idea time."""
    if not new_papers:
        return
    from app.services import paper_db

    cache_dir = discovery_dir(project.owner_id, project.id) / "fulltext"
    ordered = sorted(new_papers, key=lambda p: p.relevance or 0.0, reverse=True)
    deadline = time.monotonic() + 1800  # 30 min, well under the RQ job timeout
    _append(
        db, job,
        f"Building 5-point summaries for {len(ordered)} new papers (paper DB; "
        "reused when already summarized)...",
        progress=72,
    )
    done = 0
    for paper in ordered:
        raise_if_canceled(db, job)
        try:
            doc = paper_db.convert_and_store(db, project.id, paper, llm, cache_dir, "discovered")
            paper.document_id = doc.id
            db.commit()  # persist the link immediately so a later failure can't lose it
            done += 1
        except Exception as exc:  # noqa: BLE001 — one bad paper shouldn't fail discovery
            db.rollback()
            logger.warning("paper-db build skipped for '%s': %s", (paper.title or "")[:60], exc)
        if done and done % 5 == 0:
            _set(db, job, progress=min(79, 72 + done // 3))
        if time.monotonic() > deadline:
            _append(
                db, job,
                f"Time budget reached; {done}/{len(ordered)} papers 5-point-summarized "
                "(the rest keep the quick summary for now).",
            )
            break


def _update_context(db: Session, project: Project) -> None:
    """Refresh project context after discovery (best-effort)."""
    try:
        from app.services.context_service import update_after

        update_after(db, project, "discovery")
    except Exception:  # noqa: BLE001 — context is best-effort, never fails the job
        logger.debug("context update skipped", exc_info=True)


# --------------------------------------------------------------------------- #
# Job row helpers
# --------------------------------------------------------------------------- #
def _set(db: Session, job: Job, **fields) -> None:
    log_line = fields.pop("log", None)
    for k, v in fields.items():
        setattr(job, k, v)
    if log_line:
        job.log = (job.log or "") + log_line + "\n"
    db.commit()


def _append(db: Session, job: Job, line: str, progress: int | None = None) -> None:
    job.log = (job.log or "") + line + "\n"
    if progress is not None:
        job.progress = progress
    db.commit()


def _fail(db: Session, job: Job, message: str) -> None:
    job.status = JobStatus.failed
    job.error = message
    job.log = (job.log or "") + f"ERROR: {message}\n"
    db.commit()
