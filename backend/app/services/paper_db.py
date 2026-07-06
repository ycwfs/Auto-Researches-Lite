"""Global paper database: a deduplicated store of each explored paper's MinerU
markdown + structured 5-point summary.

`get_or_create_document` dedups by arxiv_id → doi → normalized title; `ensure_summarized`
lazily converts (MinerU → pypdf → abstract) and 5-point-summarizes a paper ONCE and reuses
the stored version on later calls and across projects; `link_project` records a project's
explored-papers set (for the Context panel). `convert_and_store` does all three.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.integrations import mineru
from app.models.content import PaperDocument, ProjectDocumentRef
from app.services.llm import _mock_full_summary

logger = logging.getLogger("far.paper_db")

# extraction_method values that mean REAL parsed full text (vs the abstract fallback).
# Gates the per-paper chat and the "Re-parse full text" affordance.
FULLTEXT_METHODS = ("mineru", "pypdf", "upload")


def has_real_fulltext(method: str | None, markdown: str | None) -> bool:
    return bool((markdown or "").strip() and (method or "") in FULLTEXT_METHODS)


# Canonical arXiv id shapes (new "2401.12345" and old "cond-mat/9901001").
_ARXIV_NEW = re.compile(r"^\d{4}\.\d{4,5}$")
_ARXIV_OLD = re.compile(r"^[a-z-]+(?:\.[A-Z]{2})?/\d{7}$")


def _norm_title(title: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^0-9a-z ]", "", (title or "").lower())).strip()[:300]


def _get(meta: Any, *names: str) -> str:
    for n in names:
        v = meta.get(n) if isinstance(meta, dict) else getattr(meta, n, None)
        if v:
            return str(v).strip()
    return ""


def _authors(meta: Any) -> list:
    v = meta.get("authors") if isinstance(meta, dict) else getattr(meta, "authors", None)
    return list(v or [])


def _arxiv_id(meta: Any) -> str:
    """Normalize a real arXiv id (strip a version suffix and an arxiv.org URL prefix);
    return "" for non-arXiv ids (DOIs, Semantic Scholar hashes, …) so they don't get
    mangled into a bogus dedup key — those fall through to DOI/title dedup."""
    raw = _get(meta, "arxiv_id", "id").strip()
    if not raw:
        return ""
    m = re.search(r"arxiv\.org/(?:abs|pdf)/(.+)$", raw, re.IGNORECASE)
    if m:  # only strip a genuine arXiv URL prefix, not an arbitrary slash
        raw = m.group(1)
    base = re.sub(r"v\d+$", "", raw)
    if _ARXIV_NEW.match(base) or _ARXIV_OLD.match(base):
        return base
    return raw if (_ARXIV_NEW.match(raw) or _ARXIV_OLD.match(raw)) else ""


def find_document(db: Session, meta: Any) -> PaperDocument | None:
    """Read-only dedup lookup: the existing PaperDocument for a paper, or None.
    Same matching as get_or_create_document (arxiv_id → doi → title_key) but never
    creates — used to REUSE already-parsed full text without re-parsing."""
    arxiv_id = _arxiv_id(meta)
    doi = _get(meta, "doi", "DOI")
    title_key = _norm_title(_get(meta, "title"))
    doc = None
    if arxiv_id:
        doc = db.query(PaperDocument).filter(PaperDocument.arxiv_id == arxiv_id).first()
    if doc is None and doi:
        doc = db.query(PaperDocument).filter(PaperDocument.doi == doi).first()
    if doc is None and title_key:
        doc = db.query(PaperDocument).filter(PaperDocument.title_key == title_key).first()
    return doc


def get_or_create_document(db: Session, meta: Any) -> PaperDocument:
    """Find (dedup) or create the global PaperDocument for a paper.

    `meta` is a Paper model or a dict with any of: arxiv_id/id, doi/DOI, title,
    authors, year/published, abstract, pdf_url/url, source.
    """
    arxiv_id = _arxiv_id(meta)
    doi = _get(meta, "doi", "DOI")
    title = _get(meta, "title")
    title_key = _norm_title(title)

    doc = find_document(db, meta)
    if doc is not None:
        return doc

    doc = PaperDocument(
        arxiv_id=arxiv_id,
        doi=doi,
        title=title or "(untitled)",
        title_key=title_key,
        authors=_authors(meta),
        year=(_get(meta, "year", "published") or "")[:4],
        abstract=_get(meta, "abstract"),
        pdf_url=_get(meta, "pdf_url", "url"),
        source=_get(meta, "source") or "unknown",
    )
    db.add(doc)
    try:
        db.flush()
    except IntegrityError:
        # A concurrent run inserted the same paper first (partial unique indexes on
        # arxiv_id/doi/title_key). Converge on the winner instead of duplicating.
        db.rollback()
        doc = None
        if arxiv_id:
            doc = db.query(PaperDocument).filter(PaperDocument.arxiv_id == arxiv_id).first()
        if doc is None and doi:
            doc = db.query(PaperDocument).filter(PaperDocument.doi == doi).first()
        if doc is None and title_key:
            doc = db.query(PaperDocument).filter(PaperDocument.title_key == title_key).first()
        if doc is None:
            raise
    return doc


# Per-paper MinerU wait for BULK conversion (idea grounding, discovery): one slow/
# stuck PDF must not stall a 60-paper loop for the admin's on-demand budget. The full
# admin budget is reserved for the on-demand "Re-parse" action (max_wait=None).
BULK_MINERU_WAIT = 150


def ensure_converted(
    db: Session, doc: PaperDocument, cache_dir: Path, force: bool = False,
    max_wait: int | None = None,
) -> PaperDocument:
    """MinerU-convert the document to markdown once — shared + prompt-independent, so it's
    always stored on the global PaperDocument regardless of which project triggered it.

    `force` re-runs extraction even when markdown already exists AND busts the file
    cache — the recovery path for a paper that previously fell back to the abstract
    (a transient MinerU miss). `max_wait` bounds the MinerU poll: None uses the admin
    budget (on-demand recovery); a bulk caller passes a small cap so one stuck PDF
    can't block the whole loop.

    A forced re-extract only ever UPGRADES: it will not re-fetch over a user upload,
    and it will not downgrade real full text back to the abstract fallback when the
    re-fetch fails (an unfetchable URL, e.g. OpenReview, or a transient MinerU miss)."""
    if not force and (doc.markdown or "").strip():
        return doc
    # A user-uploaded full text is authoritative, and the PDF can't be fetched
    # server-side (that's the whole reason it was uploaded). A forced re-extract
    # would only re-fetch the unfetchable URL and clobber the upload with the
    # abstract fallback — so never re-parse over an upload.
    if doc.extraction_method == "upload" and (doc.markdown or "").strip():
        return doc
    from app.services import integration_service

    mineru_key, mineru_url = integration_service.mineru_config(db)
    wait = integration_service.mineru_max_wait(db) if max_wait is None else max_wait
    res = mineru.extract(
        {"pdf_url": doc.pdf_url, "abstract": doc.abstract, "title": doc.title, "arxiv_id": doc.arxiv_id},
        cache_dir,
        api_key=mineru_key,
        api_url=mineru_url,
        max_wait=wait,
        force=force,
    )
    # Don't downgrade: if a forced re-extract fell back to the abstract but we
    # already hold real full text (a prior successful parse or an upload), keep it.
    had_fulltext = doc.extraction_method in FULLTEXT_METHODS and (doc.markdown or "").strip()
    if res.method == "abstract" and had_fulltext:
        logger.info(
            "re-extract of doc %s fell back to the abstract; keeping existing '%s' full text",
            doc.id, doc.extraction_method,
        )
        return doc
    doc.markdown = res.text
    doc.extraction_method = res.method
    if res.method == "abstract":
        # A FORCED re-parse that still can't get full text means the URL is
        # unfetchable and the paper isn't on arXiv — mark it so the frontend stops
        # auto-retrying. (A first, non-forced abstract fallback stays recoverable so
        # the one on-demand auto-retry can still run.)
        if force:
            doc.fulltext_recoverable = False
    else:
        doc.fulltext_recoverable = True  # real full text obtained → recovered
    # Persist an accessible link when the arXiv-by-title fallback parsed a different
    # (arXiv) URL than the paper's own — so Zotero can link to arXiv, not the
    # unfetchable OpenReview URL.
    if res.source_url and "arxiv.org" in res.source_url and res.source_url != (doc.pdf_url or ""):
        doc.resolved_pdf_url = res.source_url
    db.commit()
    return doc


def ensure_summarized(
    db: Session, doc: PaperDocument, llm, cache_dir: Path, summary_prompt: str | None = None,
    force: bool = False,
) -> PaperDocument:
    """Convert + summarize the document once; reuse if already done (dedup). Writes the
    SHARED default summary on the PaperDocument — callers with a custom prompt use
    set_project_summary instead so a project's prompt can't overwrite the shared default."""
    ensure_converted(db, doc, cache_dir)
    offline = bool(getattr(llm, "offline", False))
    # (Re)summarize when there's no summary yet, or when a prior run could only store
    # a mock summary and we now have a real provider available (upgrade it).
    if force or not (doc.summary or "").strip() or (doc.summary_model == "mock" and not offline):
        text_src = doc.markdown or doc.abstract or doc.title
        summary = llm.summarize_full_text(text_src, prompt=summary_prompt)
        # A real-provider failure falls back to the mock body; mark it so a later run
        # with a working LLM retries instead of permanently keeping the mock.
        is_mock = offline or summary == _mock_full_summary(text_src)
        doc.summary = summary
        doc.summary_model = "mock" if is_mock else getattr(llm.config, "provider", "")
        db.commit()
    return doc


# --------------------------------------------------------------------------- #
# Per-project Summary / code override (so one user's prompt can't overwrite another's
# shared summary). Each field falls back to the document's shared default when unset.
# --------------------------------------------------------------------------- #
def _get_or_create_override(db: Session, project_id: int, document_id: int):
    from app.models.content import ProjectPaperSummary

    ov = (
        db.query(ProjectPaperSummary)
        .filter(ProjectPaperSummary.project_id == project_id,
                ProjectPaperSummary.document_id == document_id)
        .first()
    )
    if ov is None:
        ov = ProjectPaperSummary(project_id=project_id, document_id=document_id)
        db.add(ov)
    return ov


def _merge_view(ov, doc: PaperDocument | None) -> dict:
    """Resolve each field to the project override (when set) else the shared default."""
    d_sum = (doc.summary if doc else "") or ""
    d_sm = (doc.summary_model if doc else "") or ""
    d_cu = (doc.code_url if doc else "") or ""
    d_cs = (doc.code_summary if doc else "") or ""
    d_cst = (doc.code_status if doc else "") or ""
    d_cm = (doc.code_model if doc else "") or ""
    if ov is None:
        return {"summary": d_sum, "summary_model": d_sm, "code_url": d_cu,
                "code_summary": d_cs, "code_status": d_cst, "code_model": d_cm}
    has_sum = bool((ov.summary or "").strip())
    has_code = bool((ov.code_status or "").strip())
    return {
        "summary": (ov.summary if has_sum else d_sum) or "",
        "summary_model": (ov.summary_model if has_sum else d_sm) or "",
        "code_url": (ov.code_url if has_code else d_cu) or "",
        "code_summary": (ov.code_summary if has_code else d_cs) or "",
        "code_status": (ov.code_status if has_code else d_cst) or "",
        "code_model": (ov.code_model if has_code else d_cm) or "",
    }


def project_view(db: Session, project_id: int, doc: PaperDocument | None) -> dict:
    """The project's effective Summary/code fields for a (shared) document."""
    if doc is None:
        return _merge_view(None, None)
    from app.models.content import ProjectPaperSummary

    ov = (
        db.query(ProjectPaperSummary)
        .filter(ProjectPaperSummary.project_id == project_id,
                ProjectPaperSummary.document_id == doc.id)
        .first()
    )
    return _merge_view(ov, doc)


def overrides_map(db: Session, project_id: int, doc_ids) -> dict:
    """Batch {document_id: ProjectPaperSummary} for the project's overrides among doc_ids."""
    ids = [i for i in doc_ids if i]
    if not ids:
        return {}
    from app.models.content import ProjectPaperSummary

    rows = (
        db.query(ProjectPaperSummary)
        .filter(ProjectPaperSummary.project_id == project_id,
                ProjectPaperSummary.document_id.in_(ids))
        .all()
    )
    return {r.document_id: r for r in rows}


def set_project_summary(
    db: Session, project_id: int, doc: PaperDocument, llm, prompt: str | None, force: bool = False
):
    """Compute + store the project's OWN full-text Summary for doc (custom prompt). Dedups:
    reuses an existing override unless `force` (the re-summarize button) re-runs it — so a
    custom-prompt project doesn't re-summarize every paper on every discovery/idea run."""
    ov = _get_or_create_override(db, project_id, doc.id)
    if not force and (ov.summary or "").strip():
        return ov
    offline = bool(getattr(llm, "offline", False))
    text_src = doc.markdown or doc.abstract or doc.title
    summary = llm.summarize_full_text(text_src, prompt=prompt)
    is_mock = offline or summary == _mock_full_summary(text_src)
    ov.summary = summary
    ov.summary_model = "mock" if is_mock else getattr(llm.config, "provider", "")
    db.commit()
    return ov


def set_project_code(
    db: Session, project_id: int, doc: PaperDocument, llm, prompt: str | None,
    force: bool = False, repo_url: str | None = None,
):
    """Compute + store the project's OWN code analysis for doc (custom prompt). Dedups on
    code_status unless `force` (the re-analyze button). `repo_url` — the manual Code
    Analysis action (a repo the detector missed, or one updated since discovery) —
    skips detection and analyzes exactly that repository."""
    from app.services import code_repo

    ov = _get_or_create_override(db, project_id, doc.id)
    if not force and (ov.code_status or "").strip():
        return ov
    url = repo_url or code_repo.find_repo_url(doc.markdown or "", doc.abstract or "")
    result = code_repo.analyze(url, llm, prompt=prompt) if url else None
    if result:
        ov.code_url, ov.code_summary = result
        ov.code_status = "ok"
        ov.code_model = getattr(getattr(llm, "config", None), "provider", "") or (
            "mock" if getattr(llm, "offline", False) else "")
    else:
        ov.code_url, ov.code_summary, ov.code_status, ov.code_model = "", "", "none", ""
    db.commit()
    return ov


def link_project(db: Session, project_id: int, doc: PaperDocument, origin: str) -> None:
    """Record (idempotently) that `project_id` has explored `doc`."""
    exists = (
        db.query(ProjectDocumentRef)
        .filter(
            ProjectDocumentRef.project_id == project_id,
            ProjectDocumentRef.document_id == doc.id,
        )
        .first()
    )
    if exists is None:
        db.add(ProjectDocumentRef(project_id=project_id, document_id=doc.id, origin=origin))
        try:
            db.commit()
        except IntegrityError:  # concurrent link won the race — already recorded
            db.rollback()


def convert_and_store(
    db: Session,
    project_id: int,
    meta: Any,
    llm,
    cache_dir: Path,
    origin: str,
    *,
    pre_markdown: str | None = None,
    max_wait: int | None = BULK_MINERU_WAIT,
    reuse_only: bool = False,
) -> PaperDocument:
    """Dedup-aware: get-or-create the doc, ensure it's converted + summarized, and
    link it to the project. The one entry point the ideas pipeline calls per paper.
    `max_wait` caps the per-paper MinerU poll (bulk default) so one stuck PDF can't
    stall the whole loop. `reuse_only` NEVER parses (no MinerU): it reuses full text +
    summary already in the paper DB and links the paper, leaving anything uncached for
    the caller to ground on its abstract — so idea generation can't block on parsing.

    `pre_markdown` lets a caller supply already-extracted full text (e.g. a user-uploaded
    PDF that has no downloadable URL); it seeds `doc.markdown` so `ensure_summarized` skips
    the URL/MinerU extraction path and summarizes the supplied text directly.
    """
    doc = get_or_create_document(db, meta)
    if pre_markdown and pre_markdown.strip() and not (doc.markdown or "").strip():
        doc.markdown = pre_markdown.strip()
        doc.extraction_method = "upload"
        db.flush()
    # The Summary + code-analysis prompts are per-project editable. A CUSTOM prompt writes
    # the project's OWN override (so it never overwrites another user's shared summary); the
    # DEFAULT prompt uses/populates the shared default on the document (cheap reuse). The
    # MinerU markdown is always shared (prompt-independent).
    from app.models.project import Project
    from app.services import prompts

    project = db.get(Project, project_id)
    summary_prompt = prompts.effective_template(project, "summary_5pt")
    code_prompt = prompts.effective_template(project, "code_analysis")
    summary_default = prompts.REGISTRY["summary_5pt"].default_template
    code_default = prompts.REGISTRY["code_analysis"].default_template

    if reuse_only:
        # Never parse (no MinerU). If the paper was already converted at discovery,
        # make sure its 5-point summary exists (fast, reuses the markdown); otherwise
        # leave it — the caller grounds on the abstract. Cached code analysis rides
        # along via the project view; we don't fetch a repo here either.
        if (doc.markdown or "").strip():
            if summary_prompt != summary_default:
                set_project_summary(db, project_id, doc, llm, summary_prompt)
            else:
                ensure_summarized(db, doc, llm, cache_dir, summary_prompt=summary_prompt)
        link_project(db, project_id, doc, origin)
        return doc

    ensure_converted(db, doc, cache_dir, max_wait=max_wait)
    if summary_prompt != summary_default:
        set_project_summary(db, project_id, doc, llm, summary_prompt)
    else:
        ensure_summarized(db, doc, llm, cache_dir, summary_prompt=summary_prompt)
    # Code analysis (best-effort, silent on missing/broken/empty).
    try:
        from app.services import code_repo

        if code_prompt != code_default:
            set_project_code(db, project_id, doc, llm, code_prompt)
        else:
            code_repo.ensure_analyzed(db, doc, llm, code_prompt=code_prompt)
    except Exception:  # noqa: BLE001 — never fail a paper over its repo
        logger.debug("code analysis skipped for doc %s", doc.id, exc_info=True)
    link_project(db, project_id, doc, origin)
    return doc


def explored_for_project(db: Session, project_id: int) -> list[PaperDocument]:
    """The project's explored PaperDocuments, newest-linked first (for the panel)."""
    return (
        db.query(PaperDocument)
        .join(ProjectDocumentRef, ProjectDocumentRef.document_id == PaperDocument.id)
        .filter(ProjectDocumentRef.project_id == project_id)
        .order_by(ProjectDocumentRef.added_at.desc())
        .all()
    )


def valid_repos_for_project(db: Session, project_id: int) -> list[dict]:
    """The project's explored papers that have a VALID, non-empty analyzed code repository
    (resolved code_status == 'ok' via the per-project override) — baseline candidates.
    Returns [{document_id, title, code_url, code_summary}], newest-linked first."""
    docs = explored_for_project(db, project_id)
    ov = overrides_map(db, project_id, [d.id for d in docs])
    out: list[dict] = []
    for d in docs:
        v = _merge_view(ov.get(d.id), d)
        if v["code_status"] == "ok" and (v["code_url"] or "").strip():
            out.append({
                "document_id": d.id,
                "title": d.title or "",
                "code_url": v["code_url"],
                "code_summary": v["code_summary"] or "",
            })
    return out
