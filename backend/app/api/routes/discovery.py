"""Stage 1 discovery routes (nested under a project)."""
from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user, get_owned_project, get_owned_project_flexible
from app.core.paths import discovery_dir, uploads_dir
from app.integrations.auto_research import parse_arxiv_id
from app.models.content import Paper, PaperDocument
from app.models.enums import JobType
from app.models.job import Job
from app.models.project import Project
from app.models.user import User
from app.schemas.discovery import (
    AddPaperIn,
    CodeAnalyzeIn,
    PaperDeleteIn,
    PaperOut,
    PaperResummarizeIn,
    TrendsOut,
)
from app.services import code_repo, paper_db
from app.schemas.job import JobOut
from app.services.quota import (
    check_can_add_paper,
    check_can_resummarize,
    check_can_run_discovery,
)
from app.workers.queue import find_inflight, submit

_PDF_MAX_BYTES = 30 * 1024 * 1024  # 30 MB — generous for any single paper PDF

router = APIRouter(prefix="/projects/{project_id}/discovery", tags=["discovery"])


@router.post("/run", response_model=JobOut, status_code=202)
def run_discovery(
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Job:
    # A discovery run is already queued/running for this project → return it instead of
    # starting a duplicate that would race the dedup and store every paper twice.
    inflight = find_inflight(db, project.id, JobType.discovery)
    if inflight is not None:
        return inflight
    check_can_run_discovery(db, user)
    job = Job(project_id=project.id, user_id=user.id, type=JobType.discovery)
    db.add(job)
    db.commit()
    db.refresh(job)
    submit("app.workers.tasks_discovery.run", job.id)
    return job


@router.post("/run/paper-finder", response_model=JobOut, status_code=202)
def run_paper_finder(
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Job:
    """Run ONLY the AI Paper Finder. Its corpus is fixed, so it's decoupled from the
    regular/scheduled discovery run and triggered manually here. Its own JobType means it
    can run CONCURRENTLY with a regular discovery (their dedup is serialized per project
    by a Postgres advisory lock in run_discovery). It shares the daily discovery budget."""
    inflight = find_inflight(db, project.id, JobType.paper_finder)
    if inflight is not None:
        return inflight  # a paper-finder run is already in flight for this project
    check_can_run_discovery(db, user)
    job = Job(project_id=project.id, user_id=user.id, type=JobType.paper_finder)
    db.add(job)
    db.commit()
    db.refresh(job)
    submit("app.workers.tasks_discovery.run", job.id)
    return job


@router.get("/papers", response_model=list[PaperOut])
def list_papers(
    project: Project = Depends(get_owned_project), db: Session = Depends(get_db)
) -> list[PaperOut]:
    # The 5-point summary + code analysis are lazy-loaded per paper (see
    # /papers/{id}/summary); the list carries document_id + code_status so the UI knows
    # a summary is available and whether to show the separate Code-analysis toggle.
    papers = (
        db.query(Paper)
        .filter(Paper.project_id == project.id)
        .order_by(Paper.relevance.desc(), Paper.id.desc())
        .all()
    )
    doc_ids = [p.document_id for p in papers if p.document_id]
    code: dict[int, str] = {}
    fulltext: dict[int, bool] = {}
    recoverable: dict[int, bool] = {}
    if doc_ids:
        default_status = {}
        for doc_id, status_, method, markdown, recov in (
            db.query(
                PaperDocument.id, PaperDocument.code_status,
                PaperDocument.extraction_method, PaperDocument.markdown,
                PaperDocument.fulltext_recoverable,
            )
            .filter(PaperDocument.id.in_(doc_ids))
            .all()
        ):
            default_status[doc_id] = status_ or ""
            # Real parsed full text (not the abstract fallback) → chat is grounded.
            fulltext[doc_id] = paper_db.has_real_fulltext(method, markdown)
            # NULL (legacy row) → recoverable, so the one on-demand auto-retry can run.
            recoverable[doc_id] = recov is not False
        ov = paper_db.overrides_map(db, project.id, doc_ids)  # this project's own analyses
        for doc_id in doc_ids:
            o = ov.get(doc_id)
            code[doc_id] = (
                o.code_status if o and (o.code_status or "").strip() else default_status.get(doc_id, "")
            ) or ""
    out: list[PaperOut] = []
    for p in papers:
        item = PaperOut.model_validate(p)
        item.code_status = code.get(p.document_id, "")
        item.has_fulltext = fulltext.get(p.document_id, False)
        item.fulltext_recoverable = recoverable.get(p.document_id, True)
        out.append(item)
    return out


@router.get("/papers/{paper_id}/summary")
def paper_summary(
    paper_id: int,
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
) -> dict:
    """The 5-point summary for one discovered paper (from its linked PaperDocument)."""
    paper = db.get(Paper, paper_id)
    if paper is None or paper.project_id != project.id or not paper.document_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No summary for this paper")
    doc = db.get(PaperDocument, paper.document_id)
    v = paper_db.project_view(db, project.id, doc)  # this project's override, else shared default
    return {
        "summary_5pt": v["summary"],
        "code_url": v["code_url"],
        "code_summary": v["code_summary"],
        "code_status": v["code_status"],
    }


@router.delete("/papers/{paper_id}", status_code=204)
def delete_paper(
    paper_id: int,
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
) -> None:
    """Remove a discovered paper from the project (e.g. an off-topic retrieval)."""
    paper = db.get(Paper, paper_id)
    if paper is None or paper.project_id != project.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    db.delete(paper)
    db.commit()


def _enqueue_resummarize(
    db: Session, project: Project, user: User, paper_id: int, mode: str,
    extra: dict | None = None,
) -> Job:
    paper = db.get(Paper, paper_id)
    if paper is None or paper.project_id != project.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    check_can_resummarize(db, user)
    job = Job(
        project_id=project.id,
        user_id=user.id,
        type=JobType.resummarize,
        target_id=paper_id,
        payload={"paper_id": paper_id, "mode": mode, **(extra or {})},
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    submit("app.workers.tasks_resummarize.run", job.id)
    return job


@router.post("/papers/{paper_id}/resummarize", response_model=JobOut, status_code=202)
def resummarize_paper(
    paper_id: int,
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Job:
    """Force-regenerate this paper's full-text Summary with the project's current prompt
    (debug the summary_5pt prompt). Async — returns a Job to poll."""
    return _enqueue_resummarize(db, project, user, paper_id, "full_text")


@router.post("/papers/{paper_id}/reparse", response_model=JobOut, status_code=202)
def reparse_paper(
    paper_id: int,
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Job:
    """Force a fresh MinerU parse of this paper's PDF, then re-summarize — recovers a
    paper stuck on the abstract fallback (a transient extraction miss / a slow PDF),
    retried with the admin's current MinerU poll budget, and with an arXiv-by-title
    fallback when the original URL can't be fetched. Async — returns a Job to poll."""
    return _enqueue_resummarize(db, project, user, paper_id, "full_text", extra={"reextract": True})


@router.post("/papers/{paper_id}/upload-pdf", response_model=JobOut, status_code=202)
async def upload_paper_pdf(
    paper_id: int,
    file: UploadFile = File(...),
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Job:
    """Attach a user-uploaded PDF to an EXISTING discovered paper, extract its text, and
    re-summarize — the last-resort recovery when the paper's PDF can't be fetched
    server-side (e.g. OpenReview behind Cloudflare) and it isn't on arXiv. The user
    downloads the PDF in their browser (where it works) and uploads it here."""
    from app.integrations import mineru

    paper = db.get(Paper, paper_id)
    if paper is None or paper.project_id != project.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    if file.size is not None and file.size > _PDF_MAX_BYTES:
        raise HTTPException(status_code=413, detail="PDF too large (max 30 MB).")
    data = await file.read(_PDF_MAX_BYTES + 1)
    if not data or len(data) > _PDF_MAX_BYTES:
        raise HTTPException(status_code=413, detail="PDF too large (max 30 MB).")
    if not data[:5].startswith(b"%PDF"):
        raise HTTPException(status_code=415, detail="That file is not a valid PDF.")
    text = mineru.extract_from_bytes(data)
    if not text.strip():
        raise HTTPException(
            status_code=422,
            detail="Couldn't extract text from this PDF (it may be scanned or image-only).",
        )
    check_can_resummarize(db, user)
    # Store the uploaded full text on the paper's shared document, then re-summarize.
    doc = db.get(PaperDocument, paper.document_id) if paper.document_id else None
    if doc is None:
        doc = paper_db.get_or_create_document(
            db, {"id": paper.arxiv_id, "arxiv_id": paper.arxiv_id, "title": paper.title,
                 "abstract": paper.abstract, "pdf_url": paper.pdf_url})
        paper.document_id = doc.id
        paper_db.link_project(db, project.id, doc, "upload")
    doc.markdown = text  # extract_from_bytes already bounds this
    doc.extraction_method = "upload"
    doc.fulltext_recoverable = True  # the upload IS the full text — clear any "stuck" mark
    db.commit()
    return _enqueue_resummarize(db, project, user, paper_id, "full_text")


@router.post("/papers/{paper_id}/recode", response_model=JobOut, status_code=202)
def reanalyze_code(
    paper_id: int,
    body: CodeAnalyzeIn | None = None,
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Job:
    """Force-regenerate this paper's code-repository analysis. With `body.repo_url`
    (the manual Code Analysis action) the given repository is analyzed directly —
    for repos the detector missed, or ones updated since discovery; without it the
    URL is re-detected from the paper text. Async — returns a Job to poll."""
    extra: dict = {}
    raw = (body.repo_url if body else "").strip()
    if raw:
        normalized = code_repo.normalize_repo_url(raw)
        if not normalized:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Enter a valid GitHub/GitLab repository URL (e.g. https://github.com/owner/repo)",
            )
        extra["repo_url"] = normalized
    return _enqueue_resummarize(db, project, user, paper_id, "code", extra=extra)


@router.post("/papers/resummarize", response_model=JobOut, status_code=202)
def resummarize_papers(
    body: PaperResummarizeIn,
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Job:
    """Bulk-regenerate the Summary (mode=full_text) or code analysis (mode=code) for the
    selected papers with the project's current prompt. One async Job processes them all."""
    check_can_resummarize(db, user)
    mode = "code" if body.mode == "code" else "full_text"
    payload: dict = {"paper_ids": body.paper_ids, "mode": mode}
    if body.reextract and mode == "full_text":
        payload["reextract"] = True  # force a fresh MinerU parse before summarizing
    job = Job(
        project_id=project.id,
        user_id=user.id,
        type=JobType.resummarize,
        payload=payload,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    submit("app.workers.tasks_resummarize.run", job.id)
    return job


@router.post("/papers/delete")
def delete_papers(
    body: PaperDeleteIn,
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
) -> dict:
    """Bulk-remove discovered papers from the project (e.g. all selected ones). Scoped to
    this project's own papers, so foreign ids are silently ignored. The bulk delete
    bypasses ORM before_delete events, so the papers' chat/context rows (scope
    "discovered") are purged explicitly here."""
    from app.models.context import ChatMessage, EntityContext

    ids = [
        row[0]
        for row in db.query(Paper.id)
        .filter(Paper.project_id == project.id, Paper.id.in_(body.paper_ids))
        .all()
    ]
    deleted = 0
    if ids:
        db.query(ChatMessage).filter(
            ChatMessage.scope == "discovered", ChatMessage.scope_id.in_(ids)
        ).delete(synchronize_session=False)
        db.query(EntityContext).filter(
            EntityContext.scope == "discovered", EntityContext.scope_id.in_(ids)
        ).delete(synchronize_session=False)
        deleted = db.query(Paper).filter(Paper.id.in_(ids)).delete(synchronize_session=False)
    db.commit()
    return {"deleted": int(deleted)}


def _reject_if_at_cap(db: Session, project: Project) -> None:
    cap = project.max_total_papers if project.max_total_papers is not None else 600
    count = db.query(func.count(Paper.id)).filter(Paper.project_id == project.id).scalar() or 0
    if count >= cap:
        raise HTTPException(status_code=409, detail=f"This project has reached its cap of {cap} papers.")


@router.post("/papers/add", response_model=JobOut, status_code=202)
def add_paper(
    body: AddPaperIn,
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Job:
    """Add one paper by arXiv link/ID; summarized + stored like a discovered paper (async)."""
    arxiv_id = parse_arxiv_id(body.url)
    if not arxiv_id:
        raise HTTPException(
            status_code=400, detail="Not a recognizable arXiv link or ID (e.g. 2401.01234)."
        )
    check_can_add_paper(db, user)
    _reject_if_at_cap(db, project)
    job = Job(
        project_id=project.id,
        user_id=user.id,
        type=JobType.add_paper,
        payload={"kind": "arxiv", "value": arxiv_id},
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    submit("app.workers.tasks_paper.run", job.id)
    return job


@router.post("/papers/upload", response_model=JobOut, status_code=202)
async def upload_paper(
    file: UploadFile = File(...),
    title: str = Form(""),
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Job:
    """Add one paper from an uploaded PDF; summarized + stored like a discovered paper (async)."""
    if file.size is not None and file.size > _PDF_MAX_BYTES:
        raise HTTPException(status_code=413, detail="PDF too large (max 30 MB).")
    mime = (file.content_type or "").split(";")[0].strip().lower()
    if mime not in ("application/pdf", "application/x-pdf", "application/octet-stream"):
        raise HTTPException(status_code=415, detail="Only PDF files are supported.")
    data = await file.read(_PDF_MAX_BYTES + 1)
    if not data:
        raise HTTPException(status_code=400, detail="Empty file.")
    if len(data) > _PDF_MAX_BYTES:
        raise HTTPException(status_code=413, detail="PDF too large (max 30 MB).")
    if not data[:5].startswith(b"%PDF"):
        raise HTTPException(status_code=415, detail="That file is not a valid PDF.")
    check_can_add_paper(db, user)
    _reject_if_at_cap(db, project)
    dest = uploads_dir(project.owner_id, project.id) / f"{uuid.uuid4().hex}.pdf"
    dest.write_bytes(data)
    job = Job(
        project_id=project.id,
        user_id=user.id,
        type=JobType.add_paper,
        payload={
            "kind": "pdf",
            "path": str(dest),
            "filename": file.filename or "",
            "title": (title or "").strip(),
        },
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    submit("app.workers.tasks_paper.run", job.id)
    return job


@router.get("/trends", response_model=TrendsOut)
def get_trends(project: Project = Depends(get_owned_project)) -> TrendsOut:
    out_dir = discovery_dir(project.owner_id, project.id)
    trends_file = out_dir / "trends.json"
    if not trends_file.exists():
        return TrendsOut(paper_count=0, top_keywords=[], categories={}, has_wordcloud=False)
    data = json.loads(trends_file.read_text())
    return TrendsOut(
        paper_count=data.get("paper_count", 0),
        top_keywords=data.get("top_keywords", []),
        categories=data.get("categories", {}),
        has_wordcloud=bool(data.get("wordcloud_path")),
    )


@router.get("/wordcloud")
def get_wordcloud(project: Project = Depends(get_owned_project_flexible)):
    path = discovery_dir(project.owner_id, project.id) / "wordcloud.png"
    if not path.exists():
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="No wordcloud yet")
    return FileResponse(str(path), media_type="image/png")
