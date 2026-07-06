"""Maintain a per-project context document, auto-updated after each step.

The context is the project's evolving memory: background, references, and a
summary per stage. It feeds downstream steps (ideas) and the dialogue panel, and
is refreshed by `update_after(step)` at the end of each job.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.content import Paper, PaperDocument
from app.models.context import EntityContext, ProjectContext
from app.models.project import Project

# Per-entity context scopes ("discovered" for a discovered paper on the Discover
# panel); "project" is the shared layer.
ENTITY_SCOPES = ("discovered",)


def _format_background(project: Project) -> str:
    """Background is derived from the project's metadata, so it can be regenerated
    deterministically whenever that metadata changes."""
    return (
        f"Project '{project.name}'. {project.description or ''}\n"
        f"Focus keywords: {', '.join(project.keywords or []) or 'n/a'}. "
        f"Target venue: {project.target_venue}."
    )


def get_or_create(db: Session, project: Project) -> ProjectContext:
    ctx = (
        db.query(ProjectContext)
        .filter(ProjectContext.project_id == project.id)
        .one_or_none()
    )
    if ctx is None:
        ctx = ProjectContext(
            project_id=project.id, stage="discovery", background=_format_background(project)
        )
        db.add(ctx)
        db.flush()
    return ctx


def render_context_md(db: Session, project: Project) -> str:
    """The project's context document as a single markdown file (for export)."""
    ctx = get_or_create(db, project)
    sections = [
        ("Background", ctx.background),
        ("Papers", ctx.papers_summary),
        ("References", ctx.references),
    ]
    parts = [f"# {project.name} — project context\n"]
    for title, body in sections:
        body = (body or "").strip()
        if body:
            parts.append(f"## {title}\n\n{body}\n")
    return "\n".join(parts)


def refresh_background(db: Session, project: Project) -> ProjectContext:
    """Regenerate `background` from the project's current metadata and commit.
    Called when project metadata (name/description/keywords/venue) changes so the
    context stays in sync without waiting for the next stage job."""
    ctx = get_or_create(db, project)
    ctx.background = _format_background(project)
    ctx.updated_at = datetime.now(timezone.utc)
    db.commit()
    return ctx


def update_after(db: Session, project: Project, step: str) -> ProjectContext:
    """Regenerate the section(s) affected by `step` and advance the stage."""
    ctx = get_or_create(db, project)

    # Keep background current with the project's metadata on every pass.
    ctx.background = _format_background(project)

    if step in ("discovery", "all"):
        papers = db.query(Paper).filter(Paper.project_id == project.id).order_by(
            Paper.relevance.desc()
        ).all()
        ctx.papers_summary = (
            f"{len(papers)} papers collected. Top: "
            + "; ".join(p.title for p in papers[:8])
        )
        ctx.references = _format_references(papers)

    # Advance the visible stage monotonically.
    order = ["discovery"]
    if step in order:
        cur = order.index(ctx.stage) if ctx.stage in order else 0
        ctx.stage = order[max(cur, order.index(step))]
        project.stage = ctx.stage
    ctx.updated_at = datetime.now(timezone.utc)
    db.commit()
    return ctx


def _format_references(papers: list[Paper]) -> str:
    lines = []
    for i, p in enumerate(papers, 1):
        authors = ", ".join((p.authors or [])[:3]) + (" et al." if len(p.authors or []) > 3 else "")
        year = (p.published or "")[:4]
        lines.append(f"[{i}] {authors} ({year}). {p.title}. arXiv:{p.arxiv_id}")
    note = f"({len(papers)} references; top-tier venues typically expect >= 60.)\n"
    return note + "\n".join(lines)


def build_context_text(db: Session, project: Project) -> str:
    """Flatten the context into a single string for LLM input / chat."""
    ctx = get_or_create(db, project)
    parts = [
        f"# Project context: {project.name} (stage: {ctx.stage})",
        f"## Background\n{ctx.background}",
        f"## Papers\n{ctx.papers_summary}",
    ]
    return "\n\n".join(p for p in parts if p.strip())


def build_steering_context(db: Session, project: Project) -> str:
    """A compact 'what this project cares about' brief for steering discovery
    summarization: the focus (background). Deliberately excludes the bulky reference
    list and per-paper dump, so it stays short enough to prepend to many prompts."""
    ctx = get_or_create(db, project)
    parts = []
    if ctx.background:
        parts.append(f"Project focus: {ctx.background.strip()[:600]}")
    return "\n".join(parts)[:1400]


# --------------------------------------------------------------------------- #
# Per-entity (discovered paper) context — shared discovery stays above
# --------------------------------------------------------------------------- #
def build_discovery_context(db: Session, project: Project) -> str:
    """The SHARED layer every per-entity context inherits: background + discovered-paper
    summary."""
    ctx = get_or_create(db, project)
    parts = [f"# Project: {project.name}"]
    if ctx.background:
        parts.append(f"## Background\n{ctx.background}")
    if ctx.papers_summary:
        parts.append(f"## Discovered papers\n{ctx.papers_summary}")
    return "\n\n".join(p for p in parts if p.strip())


def discovered_paper_has_fulltext(db: Session, paper: Paper) -> bool:
    """True when a discovered paper has real parsed full text (MinerU or pypdf) —
    NOT the abstract-only fallback. Gates the per-paper chat, which is only useful
    when there is a document to ground answers in."""
    from app.services import paper_db

    if not getattr(paper, "document_id", None):
        return False
    doc = db.get(PaperDocument, paper.document_id)
    return bool(doc and paper_db.has_real_fulltext(doc.extraction_method, doc.markdown))


def build_discovered_paper_context(db: Session, project: Project, paper: Paper) -> str:
    """Context for a DISCOVERED paper's chat (Discover panel): the paper's metadata,
    this project's effective structured summary + code analysis, and the FULL
    MinerU-parsed text — uncapped, so answers are grounded in the whole paper."""
    from app.services import paper_db

    parts = [
        f"# Paper: {paper.title}",
        f"Authors: {', '.join(paper.authors or [])}" if paper.authors else "",
        f"Published: {paper.published or ''} · Source: {paper.source or ''}",
    ]
    doc = db.get(PaperDocument, paper.document_id) if paper.document_id else None
    full = (doc.markdown or "").strip() if doc is not None else ""
    if doc is not None:
        view = paper_db.project_view(db, project.id, doc)
        if view.get("summary"):
            parts.append("\n## Structured summary\n" + view["summary"])
        if view.get("code_status") == "ok" and view.get("code_summary"):
            parts.append(
                f"\n## Code repository analysis ({view.get('code_url') or ''})\n"
                + view["code_summary"]
            )
    if full:
        parts.append("\n## Full text\n" + full)
    elif paper.abstract:
        parts.append("\n## Abstract\n" + paper.abstract)
    return "\n".join(p for p in parts if p)


_BUILDERS = {
    "discovered": build_discovered_paper_context,
}


def _entity_row(db: Session, scope: str, scope_id: int) -> EntityContext | None:
    return (
        db.query(EntityContext)
        .filter(EntityContext.scope == scope, EntityContext.scope_id == scope_id)
        .one_or_none()
    )


def resolve_entity_context(db: Session, project: Project, scope: str, entity) -> EntityContext:
    """The stored per-entity context row. Keeps the auto-content fresh from the chain
    unless the user has edited it (`is_custom`), in which case the edit is preserved."""
    row = _entity_row(db, scope, entity.id)
    fresh = _BUILDERS[scope](db, project, entity)
    if row is None:
        row = EntityContext(
            project_id=project.id, scope=scope, scope_id=entity.id, content=fresh, is_custom=False
        )
        db.add(row)
        try:
            db.commit()
            db.refresh(row)
        except IntegrityError:  # a concurrent first-read already created it
            db.rollback()
            row = _entity_row(db, scope, entity.id)
        return row or EntityContext(
            project_id=project.id, scope=scope, scope_id=entity.id, content=fresh, is_custom=False
        )
    if not row.is_custom and row.content != fresh:
        row.content = fresh
        row.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(row)
    return row


def set_entity_context(
    db: Session, project: Project, scope: str, entity, content: str
) -> EntityContext:
    """Persist a user-edited per-entity context (marks it custom so auto-rebuild skips it)."""
    row = _entity_row(db, scope, entity.id)
    if row is None:
        row = EntityContext(project_id=project.id, scope=scope, scope_id=entity.id)
        db.add(row)
    row.content = content
    row.is_custom = True
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    return row


def regenerate_entity_context(db: Session, project: Project, scope: str, entity) -> EntityContext:
    """Rebuild the per-entity context from the chain, discarding any user edit."""
    row = _entity_row(db, scope, entity.id)
    content = _BUILDERS[scope](db, project, entity)
    if row is None:
        row = EntityContext(project_id=project.id, scope=scope, scope_id=entity.id)
        db.add(row)
    row.content = content
    row.is_custom = False
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    return row


def entity_context_text(db: Session, project: Project, scope: str, entity) -> str:
    """Plain context string for generation/chat: the user's edit if any, else fresh build."""
    return resolve_entity_context(db, project, scope, entity).content


def _load_entity(db: Session, scope: str, scope_id: int):
    if scope == "discovered":
        return db.get(Paper, scope_id)  # a discovered paper (Discover panel)
    return None


def entity_in_project(db: Session, project: Project, scope: str, scope_id: int | None):
    """Load the scoped entity ONLY if it belongs to this project (else None). Guards
    against a foreign scope_id leaking another project's context into a chat/context."""
    if scope not in ENTITY_SCOPES or not scope_id:
        return None
    entity = _load_entity(db, scope, scope_id)
    if entity is None or getattr(entity, "project_id", None) != project.id:
        return None
    return entity


def context_for_scope(db: Session, project: Project, scope: str, scope_id: int | None) -> str:
    """Chat context for a thread: project-wide rollup for `project` scope, else the
    entity's own context (falling back to the project text if the entity isn't ours)."""
    entity = entity_in_project(db, project, scope, scope_id)
    if entity is None:
        return build_context_text(db, project)
    return entity_context_text(db, project, scope, entity)
