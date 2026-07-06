"""Project model — a research workspace owned by a user."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")

    # Stage-1 discovery configuration.
    categories: Mapped[list] = mapped_column(JSON, default=list)  # e.g. ["cs.AI", "cs.LG"]
    keywords: Mapped[list] = mapped_column(JSON, default=list)
    max_results: Mapped[int] = mapped_column(Integer, default=20)  # per-source per-run target
    # Total accumulated papers cap per project (papers accumulate across runs);
    # discovery stops adding once the project reaches this, keeping the DB bounded.
    max_total_papers: Mapped[int | None] = mapped_column(Integer, nullable=True, default=600)
    target_venue: Mapped[str] = mapped_column(String(50), default="neurips")

    # Enabled paper sources (subset of admin-enabled SourceKey values).
    paper_sources: Mapped[list] = mapped_column(JSON, default=lambda: ["arxiv"])
    # Semantic Scholar discovery tuning (nullable so the additive migration can
    # add them to existing rows; NULL falls back to the S2_* env default).
    s2_recency_days: Mapped[int | None] = mapped_column(Integer, nullable=True, default=365)
    s2_fields_of_study: Mapped[str | None] = mapped_column(String(200), nullable=True, default="")
    s2_min_citations: Mapped[int | None] = mapped_column(Integer, nullable=True, default=0)
    # AI Paper Finder (paperfinder sidecar): which conference venues to search; [] = all.
    paper_finder_venues: Mapped[list | None] = mapped_column(JSON, nullable=True, default=list)
    # AI Paper Finder semantic query, used VERBATIM as the search text (ideally a pasted
    # abstract). Empty falls back to the legacy keywords+name composition. Nullable so the
    # additive migration can add it to existing rows.
    paper_finder_query: Mapped[str | None] = mapped_column(Text, nullable=True, default="")
    # AI Paper Finder minimum cosine-similarity score (0..1). 0 = off (fixed top-N). When
    # >0 it GOVERNS retrieval: the source returns every paper scoring >= this, so one run
    # pulls the full relevant set instead of a fixed count.
    paper_finder_min_score: Mapped[float | None] = mapped_column(Float, nullable=True, default=0.0)
    # Per-source target paper count, e.g. {"arxiv": 20, "semantic_scholar": 10}. A
    # missing source key falls back to max_results.
    source_max_results: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=dict)
    # Per-step model selection: {step: {"provider": str, "model": str}}.
    step_models: Mapped[dict] = mapped_column(JSON, default=dict)
    # Per-project prompt overrides keyed by services/prompts.py registry key → the
    # full edited template (e.g. {"summary": "Summarize this paper ... {abstract}"}).
    # An absent/empty key falls back to the built-in default.
    prompt_overrides: Mapped[dict] = mapped_column(JSON, default=dict)
    # LEGACY idea-generation knobs. Idea generation has been removed from the product;
    # these columns stay for old rows and back-compatible payloads but nothing reads them.
    min_papers_for_ideas: Mapped[int] = mapped_column(Integer, default=20)
    idea_summary_limit: Mapped[int] = mapped_column(Integer, default=40)
    # Schedule: discovery {"enabled": bool, "time_utc": "HH:MM"}. `ideas_schedule` is a
    # LEGACY column (idea generation removed) kept for old rows / back-compatible payloads.
    discovery_schedule: Mapped[dict] = mapped_column(JSON, default=dict)
    ideas_schedule: Mapped[dict] = mapped_column(JSON, default=dict)
    stage: Mapped[str] = mapped_column(String(20), default="discovery")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    owner = relationship("User", back_populates="projects")
    papers = relationship("Paper", back_populates="project", cascade="all, delete-orphan")
    jobs = relationship("Job", back_populates="project", cascade="all, delete-orphan")
    context = relationship(
        "ProjectContext", back_populates="project", uselist=False, cascade="all, delete-orphan"
    )
    messages = relationship("ChatMessage", back_populates="project", cascade="all, delete-orphan")
