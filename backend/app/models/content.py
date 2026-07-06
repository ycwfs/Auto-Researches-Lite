"""Discovery content models: Paper and the global PaperDocument store."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Paper(Base):
    __tablename__ = "papers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    arxiv_id: Mapped[str] = mapped_column(String(64), index=True, default="")
    # Which source channel this paper came from: arxiv / semantic_scholar /
    # ai_paper_finder (tagged by fetch_from_sources). "" for legacy rows.
    source: Mapped[str] = mapped_column(String(40), default="")
    title: Mapped[str] = mapped_column(Text, nullable=False)
    authors: Mapped[list] = mapped_column(JSON, default=list)
    abstract: Mapped[str] = mapped_column(Text, default="")
    categories: Mapped[list] = mapped_column(JSON, default=list)
    pdf_url: Mapped[str] = mapped_column(Text, default="")
    published: Mapped[str] = mapped_column(String(40), default="")
    # Curated conference (e.g. "CVPR") from the AI Paper Finder corpus; "" for other
    # sources. Shown with the year as the card's source chip ("CVPR 2026").
    venue: Mapped[str] = mapped_column(String(80), default="")

    # Summary fields (filled by summarizer, real or mock).
    summary_en: Mapped[str] = mapped_column(Text, default="")
    summary_zh: Mapped[str] = mapped_column(Text, default="")
    # LLM/keyword relevance to the project keywords (shown as the % ring on every card).
    relevance: Mapped[float] = mapped_column(Float, default=0.0)
    # AI Paper Finder semantic similarity (cosine, 0..1) — the score the relevance
    # threshold filtered on. 0 for papers from other sources (no semantic score).
    finder_score: Mapped[float] = mapped_column(Float, default=0.0)
    # Link to the global paper document (MinerU markdown + 5-point summary), set
    # during discovery once the paper is converted+summarized. NULL = not yet done.
    document_id: Mapped[int | None] = mapped_column(
        ForeignKey("paper_documents.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    project = relationship("Project", back_populates="papers")


class PaperDocument(Base):
    """Global, deduplicated store of an explored paper: metadata + MinerU markdown
    full text + a structured 5-point summary. One row per paper across all users
    (matched by arxiv_id → doi → normalized title), so conversion and summarization
    happen once and are reused everywhere."""

    __tablename__ = "paper_documents"
    # Partial unique indexes (ignore blank keys) make dedup converge under concurrent
    # idea runs; get_or_create_document re-selects on the resulting IntegrityError.
    __table_args__ = (
        Index("uq_pd_arxiv", "arxiv_id", unique=True,
              sqlite_where=text("arxiv_id != ''"), postgresql_where=text("arxiv_id != ''")),
        Index("uq_pd_doi", "doi", unique=True,
              sqlite_where=text("doi != ''"), postgresql_where=text("doi != ''")),
        Index("uq_pd_title", "title_key", unique=True,
              sqlite_where=text("title_key != ''"), postgresql_where=text("title_key != ''")),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    arxiv_id: Mapped[str] = mapped_column(String(64), index=True, default="")
    doi: Mapped[str] = mapped_column(String(200), index=True, default="")
    title: Mapped[str] = mapped_column(Text, nullable=False)
    title_key: Mapped[str] = mapped_column(String(300), index=True, default="")  # normalized for dedup
    authors: Mapped[list] = mapped_column(JSON, default=list)
    year: Mapped[str] = mapped_column(String(8), default="")
    abstract: Mapped[str] = mapped_column(Text, default="")
    pdf_url: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(40), default="")
    markdown: Mapped[str] = mapped_column(Text, default="")  # MinerU/pypdf/abstract full text
    # An accessible PDF URL (e.g. the arXiv link) discovered when the paper's own
    # pdf_url can't be fetched server-side (OpenReview/Cloudflare). Used for the
    # Zotero attachment/link so it isn't a dead server-side link.
    resolved_pdf_url: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[str] = mapped_column(Text, default="")  # 5-point structured summary
    extraction_method: Mapped[str] = mapped_column(String(20), default="")  # mineru|pypdf|abstract|upload
    # False once a forced re-parse still couldn't fetch full text (unfetchable URL, not
    # on arXiv) — the frontend uses it to stop auto-retrying a paper that can't recover.
    # NULL on rows predating the column → treated as recoverable (one auto-retry allowed).
    fulltext_recoverable: Mapped[bool] = mapped_column(Boolean, default=True)
    summary_model: Mapped[str] = mapped_column(String(60), default="")
    # Code repository found in the paper (analogous to the 5-point summary). code_status:
    # "" not yet processed | "none" (missing/broken/empty repo) | "ok" (analyzed).
    code_url: Mapped[str] = mapped_column(Text, default="")
    code_summary: Mapped[str] = mapped_column(Text, default="")  # structured codebase analysis
    code_status: Mapped[str] = mapped_column(String(8), default="")
    code_model: Mapped[str] = mapped_column(String(60), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class ProjectDocumentRef(Base):
    """Links a project to the global PaperDocuments it has explored — the project's
    'explored papers' set powering the Context panel. Many-to-many; all paper
    metadata/markdown/summary lives on the shared PaperDocument."""

    __tablename__ = "project_document_refs"
    __table_args__ = (UniqueConstraint("project_id", "document_id", name="uq_project_document"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    document_id: Mapped[int] = mapped_column(
        ForeignKey("paper_documents.id", ondelete="CASCADE"), index=True
    )
    origin: Mapped[str] = mapped_column(String(20), default="")  # discovered|zotero
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ProjectPaperSummary(Base):
    """A project's OWN Summary / code analysis for a shared PaperDocument, used when the
    project summarizes with a custom prompt or re-summarizes via the per-paper buttons.
    The expensive MinerU markdown stays shared on the PaperDocument; only the
    prompt-dependent OUTPUT is isolated here so one user's prompt can't overwrite another's.
    Each field falls back to the document's shared default when unset (see paper_db.project_view)."""

    __tablename__ = "project_paper_summaries"
    __table_args__ = (UniqueConstraint("project_id", "document_id", name="uq_project_paper_summary"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    document_id: Mapped[int] = mapped_column(
        ForeignKey("paper_documents.id", ondelete="CASCADE"), index=True
    )
    summary: Mapped[str] = mapped_column(Text, default="")
    summary_model: Mapped[str] = mapped_column(String(60), default="")
    code_url: Mapped[str] = mapped_column(Text, default="")
    code_summary: Mapped[str] = mapped_column(Text, default="")
    code_status: Mapped[str] = mapped_column(String(8), default="")  # ok|none|"" (unset)
    code_model: Mapped[str] = mapped_column(String(60), default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
