"""Manually add ONE paper (arXiv link/ID or uploaded PDF) and run it through the
same summarize-and-store pipeline discovery uses, so it displays identically.

Runs as a background job (JobType.add_paper) because a single paper still triggers a
PDF fetch/parse plus 1-2 LLM calls plus a best-effort code-repo clone — too long to
hold an HTTP request. The route validates input and enqueues; `run_add_paper` does the
work and drives the Job row, mirroring `discovery_service.run_discovery`.
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.paths import discovery_dir
from app.integrations import auto_research, mineru
from app.models.content import Paper, PaperDocument
from app.models.enums import JobStatus
from app.models.job import Job
from app.models.project import Project
from app.services import context_service, paper_db, prompts
from app.services.discovery_service import _dedup_key  # canonical per-project dedup identity
from app.services.job_control import JobCanceled, mark_canceled, raise_if_canceled
from app.services.model_select import build_llm_for_step

logger = logging.getLogger("far.add_paper")


class IngestError(Exception):
    """A user-facing failure (bad link, unreadable PDF, cap reached) shown on the Job."""


# --------------------------------------------------------------------------- #
# Job entrypoint
# --------------------------------------------------------------------------- #
def run_add_paper(db: Session, job_id: int) -> None:
    """Execute an add-paper job; updates the Job row throughout."""
    job = db.get(Job, job_id)
    if job is None:
        return
    project = db.get(Project, job.project_id)
    if project is None:
        _fail(db, job, "Project not found")
        return
    try:
        raise_if_canceled(db, job)  # canceled before pickup — don't resurrect it
        payload = job.payload or {}
        kind = payload.get("kind")
        _set(db, job, status=JobStatus.running, progress=10, log="Fetching paper…")
        llm = build_llm_for_step(db, job.user_id, project, "summary")
        steer = context_service.build_steering_context(db, project)
        if kind == "arxiv":
            paper, note = _ingest_arxiv(db, job, project, llm, steer, str(payload.get("value", "")))
        elif kind == "pdf":
            paper, note = _ingest_pdf(db, job, project, llm, steer, payload)
        else:
            _fail(db, job, "Unknown add-paper request.")
            return
        job.target_id = paper.id
        # Refresh the project context rollup so the new paper feeds chat/steering now
        # (best-effort, exactly like discovery's tail).
        try:
            context_service.update_after(db, project, "discovery")
        except Exception:  # noqa: BLE001 — context is best-effort, never fails the add
            logger.debug("context update skipped after add", exc_info=True)
        _set(
            db, job, status=JobStatus.succeeded, progress=100,
            log=note or f"Added: {(paper.title or '')[:80]}",
        )
    except JobCanceled:
        mark_canceled(db, job, "Canceled by user.")
    except IngestError as exc:
        _fail(db, job, str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("add_paper job %s failed", job_id)
        _fail(db, job, f"{type(exc).__name__}: {exc}")


# --------------------------------------------------------------------------- #
# Source branches
# --------------------------------------------------------------------------- #
def _ingest_arxiv(db, job, project, llm, steer, raw):
    arxiv_id = auto_research.parse_arxiv_id(raw) or raw.strip()
    p = auto_research.fetch_arxiv_paper(arxiv_id)
    if p is None or not (p.get("title") or "").strip():
        raise IngestError("Couldn't fetch that arXiv paper — check the link or ID.")
    _append(db, job, f"Fetched: {p['title'][:80]}", progress=35)
    return _persist(db, job, project, llm, steer, p, origin="arxiv", pre_markdown=None)


def _ingest_pdf(db, job, project, llm, steer, payload):
    path = Path(str(payload.get("path", "")))
    if not path.exists():
        raise IngestError("The uploaded file is no longer available — please try again.")
    try:
        data = path.read_bytes()
    finally:
        # The bytes are copied into the document's markdown below; the raw file is
        # never needed again, so don't leave it lingering under uploads_dir.
        try:
            path.unlink()
        except OSError:
            pass
    text = mineru.extract_from_bytes(data)
    if not text.strip():
        raise IngestError("Couldn't extract text from this PDF (it may be scanned or image-only).")
    _append(db, job, f"Extracted {len(text)} characters from the PDF.", progress=35)
    title = (
        (payload.get("title") or "").strip()
        or _title_from_text(text)
        or (payload.get("filename") or "Uploaded paper")
    )
    # A content hash gives each upload a stable global identity, so two distinct PDFs
    # never merge in the deduped paper store (and an identical re-upload converges).
    p = {
        "id": "",
        "doi": "upload-" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:12],
        "title": title[:300],
        "authors": [],
        "abstract": _abstract_from_text(text),
        "categories": [],
        "pdf_url": "",
        "published": "",
        "source": "upload",
    }
    return _persist(db, job, project, llm, steer, p, origin="upload", pre_markdown=text)


# --------------------------------------------------------------------------- #
# Shared persist (mirrors discovery_service's per-paper body)
# --------------------------------------------------------------------------- #
def _persist(db, job, project, llm, steer, p, *, origin, pre_markdown):
    """Dedup, cap-check, summarize, build the Paper row + global PaperDocument."""
    raise_if_canceled(db, job)
    project_papers = db.query(Paper).filter(Paper.project_id == project.id).all()
    existing = {_dedup_key(pp.arxiv_id, pp.title) for pp in project_papers}
    ident = _dedup_key(str(p.get("id", "")), p.get("title", ""))
    if ident and ident in existing:
        dup = next((pp for pp in project_papers if _dedup_key(pp.arxiv_id, pp.title) == ident), None)
        if dup is not None:
            return dup, "This paper is already in the project."
    cap = project.max_total_papers if project.max_total_papers is not None else 600
    if len(existing) >= cap:
        raise IngestError(f"This project has reached its cap of {cap} papers.")

    raise_if_canceled(db, job)
    _append(db, job, "Summarizing…", progress=55)
    summary = llm.summarize_paper(p, project.keywords or [], context=steer)
    paper = Paper(
        project_id=project.id,
        arxiv_id=str(p.get("id", "")),
        source=str(p.get("source", "")),
        title=(p.get("title", "") or "").strip(),
        authors=p.get("authors", []) or [],
        abstract=p.get("abstract", "") or "",
        categories=p.get("categories", []) or [],
        pdf_url=p.get("pdf_url", "") or "",
        published=p.get("published", "") or "",
        summary_en=summary["summary_en"],
        summary_zh=summary["summary_zh"],
        relevance=summary["relevance"],
    )
    # Persist the Paper row first so it shows even if the heavier doc build fails below.
    db.add(paper)
    db.commit()
    db.refresh(paper)

    raise_if_canceled(db, job)
    _append(db, job, "Building full-text summary + code analysis…", progress=78)
    try:
        cache_dir = discovery_dir(project.owner_id, project.id) / "fulltext"
        # Pass the source dict (carries the arXiv id / upload content-hash doi) so the
        # global PaperDocument dedups by a real identity, not just the title.
        doc = paper_db.convert_and_store(
            db, project.id, p, llm, cache_dir, origin, pre_markdown=pre_markdown
        )
        paper.document_id = doc.id
        db.commit()
    except Exception as exc:  # noqa: BLE001 — a bad PDF/repo must not lose the added paper
        db.rollback()
        logger.warning("paper-document build skipped for manual add: %s", exc)
    return paper, ""


# --------------------------------------------------------------------------- #
# PDF title/abstract heuristics (metadata for the digest + dedup; the 5-point
# summary always runs over the full extracted text)
# --------------------------------------------------------------------------- #
def _title_from_text(text: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if len(line) >= 8 and not line.lower().startswith(("arxiv:", "http")):
            return line[:300]
    return ""


def _abstract_from_text(text: str) -> str:
    low = text.lower()
    idx = low.find("abstract")
    if 0 <= idx < 4000:
        chunk = text[idx + len("abstract") : idx + 2000].lstrip(" :—-\n\t")
        if chunk.strip():
            return chunk.strip()[:1500]
    return text.strip()[:1500]


# --------------------------------------------------------------------------- #
# Job row helpers (mirror discovery_service)
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


def _ensure_doc(db: Session, project: Project, paper: Paper, cache_dir, *, force_convert: bool = False):
    """The paper's global PaperDocument (create + link if never converted), with markdown.
    `force_convert` re-parses the PDF even if markdown exists (retry a failed extract)."""
    doc = db.get(PaperDocument, paper.document_id) if paper.document_id else None
    if doc is None:
        meta = {"id": paper.arxiv_id, "arxiv_id": paper.arxiv_id, "title": paper.title,
                "abstract": paper.abstract, "pdf_url": paper.pdf_url}
        doc = paper_db.get_or_create_document(db, meta)
        paper.document_id = doc.id
        db.commit()
        paper_db.link_project(db, project.id, doc, "resummarize")
    paper_db.ensure_converted(db, doc, cache_dir, force=force_convert)  # shared markdown
    return doc


def run_resummarize(db: Session, job_id: int) -> None:
    """Force a re-run of one OR many papers' full-text Summary / code-repository analysis with
    the project's CURRENT editable prompt — the per-paper buttons and the bulk
    'Re-summarize Selected …' actions. Always writes THIS project's per-paper override (never
    the shared document), so it can't change another user's view. payload carries `paper_ids`
    (bulk) or `paper_id` (single) + `mode` (full_text|code)."""
    job = db.get(Job, job_id)
    if job is None:
        return
    project = db.get(Project, job.project_id)
    if project is None:
        _fail(db, job, "Project not found")
        return
    payload = job.payload or {}
    raw_ids = payload.get("paper_ids") or (
        [payload["paper_id"]] if payload.get("paper_id") is not None else [])
    is_code = payload.get("mode") == "code"
    reextract = bool(payload.get("reextract"))  # force a fresh MinerU parse first
    papers = [p for p in (db.get(Paper, pid) for pid in raw_ids)
              if p is not None and p.project_id == project.id]
    if not papers:
        _fail(db, job, "No papers to re-summarize")
        return
    try:
        raise_if_canceled(db, job)
        llm = build_llm_for_step(db, job.user_id, project, "summary")
        cache_dir = discovery_dir(project.owner_id, project.id) / "fulltext"
        summary_prompt = prompts.effective_template(project, "summary_5pt")
        code_prompt = prompts.effective_template(project, "code_analysis")
        n, done = len(papers), 0
        for paper in papers:
            raise_if_canceled(db, job)
            action = "re-parsing + summarizing" if reextract else (
                "analyzing code" if is_code else "summarizing")
            _set(db, job, status=JobStatus.running, progress=5 + int(90 * done / n),
                 log=f"Re-{action} ({done + 1}/{n}): {(paper.title or '')[:60]}…")
            try:
                # reextract forces a fresh MinerU parse (retry a failed extraction),
                # which recovers papers that had fallen back to the abstract.
                doc = _ensure_doc(db, project, paper, cache_dir, force_convert=reextract)
                if reextract:
                    _append(db, job, f"  parsed via {doc.extraction_method or 'abstract'} "
                                     f"({len(doc.markdown or '')} chars)")
                if is_code:
                    paper_db.set_project_code(
                        db, project.id, doc, llm, code_prompt, force=True,
                        repo_url=str(payload.get("repo_url") or "") or None,
                    )
                else:
                    paper_db.set_project_summary(db, project.id, doc, llm, summary_prompt, force=True)
                done += 1
            except Exception as exc:  # noqa: BLE001 — one bad paper/repo shouldn't fail the batch
                db.rollback()
                logger.warning("resummarize skipped paper %s: %s", paper.id, exc)
        job.target_id = papers[0].id if n == 1 else None
        what = "code analysis" if is_code else "summary"
        note = (f"Regenerated the {what} for {done} paper{'' if done == 1 else 's'}."
                if n != 1 else (f"{what.capitalize()} regenerated." if done else
                                f"No {what} could be regenerated."))
        _set(db, job, status=JobStatus.succeeded, progress=100, log=note)
    except JobCanceled:
        mark_canceled(db, job, "Canceled by user.")
    except Exception as exc:  # noqa: BLE001 — a bad batch must not crash the worker
        logger.exception("resummarize job %s failed", job_id)
        _fail(db, job, f"{type(exc).__name__}: {exc}")
