"""Discovery result schemas."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PaperOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    arxiv_id: str
    source: str = ""
    title: str
    authors: list[str]
    abstract: str
    categories: list[str]
    pdf_url: str
    published: str
    venue: str = ""  # curated conference (e.g. "CVPR"); shown with the year on the card
    summary_en: str
    summary_zh: str
    relevance: float
    # AI Paper Finder semantic similarity (cosine); 0 for papers from other sources.
    finder_score: float = 0.0
    document_id: int | None = None  # link to the global PaperDocument (its 5-point summary
    # is lazy-loaded via /papers/{id}/summary); presence signals a summary is available
    # Whether a code-repository analysis exists ("ok") for this paper's document — lets the
    # card show a separate Code-analysis toggle. "" / "none" = no analyzed repo.
    code_status: str = ""
    # Whether the paper has parsed full text (MinerU/pypdf, not the abstract fallback) —
    # gates the per-paper chat, which needs a document to ground answers in.
    has_fulltext: bool = False
    # When the paper was discovered (added to the project). Optional: legacy rows
    # predating the column may be NULL.
    created_at: datetime | None = None

    # `source` / `venue` are NULL on rows created before those columns existed.
    @field_validator("source", "venue", mode="before")
    @classmethod
    def _none_to_empty(cls, v: str | None) -> str:
        return v or ""

    # finder_score is NULL on every paper discovered before the column was added
    # (the additive migration doesn't backfill); treat NULL as 0 (no semantic score).
    @field_validator("finder_score", mode="before")
    @classmethod
    def _none_to_zero(cls, v: float | None) -> float:
        return v or 0.0


class AddPaperIn(BaseModel):
    """Manually add a paper by arXiv link or bare ID."""

    url: str = Field(..., min_length=1, max_length=400)


class PaperDeleteIn(BaseModel):
    """Bulk-remove discovered papers by id (e.g. the selected set)."""

    paper_ids: list[int] = Field(..., min_length=1, max_length=2000)


class PaperResummarizeIn(BaseModel):
    """Bulk re-summarize discovered papers by id (the selected set)."""

    paper_ids: list[int] = Field(..., min_length=1, max_length=500)
    mode: str = Field(default="full_text")  # "full_text" | "code"
    reextract: bool = False  # full_text only: force a fresh MinerU parse first


class CodeAnalyzeIn(BaseModel):
    """Manual per-paper code analysis: the user supplies the repository URL (the
    detector missed it, or the repo changed since discovery). Empty → re-detect."""

    repo_url: str = Field(default="", max_length=400)


class PaperDocumentOut(BaseModel):
    """A globally-stored explored paper: metadata + its 5-point summary (no markdown
    body — that can be large; `has_markdown` flags whether full text was captured)."""

    id: int
    arxiv_id: str = ""
    doi: str = ""
    title: str
    authors: list[str] = Field(default_factory=list)
    year: str = ""
    source: str = ""
    summary: str = ""
    extraction_method: str = ""
    has_markdown: bool = False
    code_url: str = ""
    code_summary: str = ""
    code_status: str = ""  # "" not processed | "none" | "ok"
    created_at: datetime


class TrendKeyword(BaseModel):
    term: str
    weight: float


class TrendsOut(BaseModel):
    paper_count: int
    top_keywords: list[TrendKeyword]
    categories: dict[str, int]
    has_wordcloud: bool
