"""Project schemas."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    categories: list[str] = Field(default_factory=lambda: ["cs.AI", "cs.LG"])
    keywords: list[str] = Field(default_factory=list)
    max_results: int = Field(default=20, ge=1, le=100)
    max_total_papers: int = Field(default=600, ge=0, le=600)
    target_venue: str = "neurips"
    paper_sources: list[str] = Field(default_factory=lambda: ["arxiv"])
    # Semantic Scholar tuning: recency window (0 = no limit), field-of-study
    # filter ("" / "auto" = derive, "off" = disable, else explicit), min citations.
    s2_recency_days: int = Field(default=365, ge=0, le=3650)
    s2_fields_of_study: str = Field(default="", max_length=200)
    s2_min_citations: int = Field(default=0, ge=0, le=100000)
    paper_finder_venues: list[str] = Field(default_factory=list)
    # AI Paper Finder semantic query (sent verbatim; ideally a pasted abstract). The
    # creation UI requires it; the schema keeps a default so programmatic/test creation
    # still works and the source falls back to keywords+name when it is empty.
    paper_finder_query: str = ""
    # AI Paper Finder min similarity score (0..1). 0 = off; >0 governs retrieval.
    paper_finder_min_score: float = Field(default=0.0, ge=0.0, le=1.0)
    source_max_results: dict[str, int] = Field(default_factory=dict)
    step_models: dict = Field(default_factory=dict)
    idea_summary_limit: int = Field(default=40, ge=1, le=200)
    discovery_schedule: dict = Field(default_factory=dict)
    ideas_schedule: dict = Field(default_factory=dict)


class ProjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    categories: list[str] | None = None
    keywords: list[str] | None = None
    max_results: int | None = Field(default=None, ge=1, le=100)
    max_total_papers: int | None = Field(default=None, ge=0, le=600)
    target_venue: str | None = None
    paper_sources: list[str] | None = None
    s2_recency_days: int | None = Field(default=None, ge=0, le=3650)
    s2_fields_of_study: str | None = Field(default=None, max_length=200)
    s2_min_citations: int | None = Field(default=None, ge=0, le=100000)
    paper_finder_venues: list[str] | None = None
    paper_finder_query: str | None = None
    paper_finder_min_score: float | None = Field(default=None, ge=0.0, le=1.0)
    source_max_results: dict[str, int] | None = None
    step_models: dict | None = None
    idea_summary_limit: int | None = Field(default=None, ge=1, le=200)
    discovery_schedule: dict | None = None
    ideas_schedule: dict | None = None


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    description: str
    categories: list[str]
    keywords: list[str]
    max_results: int
    max_total_papers: int | None = None
    target_venue: str
    paper_sources: list[str]
    s2_recency_days: int | None = None
    s2_fields_of_study: str | None = None
    s2_min_citations: int | None = None
    paper_finder_venues: list[str] | None = None
    paper_finder_query: str = ""
    paper_finder_min_score: float = 0.0
    source_max_results: dict[str, int] | None = None
    step_models: dict
    idea_summary_limit: int = 40
    discovery_schedule: dict
    ideas_schedule: dict
    stage: str
    created_at: datetime
    updated_at: datetime

    # `idea_summary_limit` is NULL on project rows created before the column existed
    # (the additive migration adds it nullable) — coerce to the default.
    @field_validator("idea_summary_limit", mode="before")
    @classmethod
    def _default_summary_limit(cls, v: int | None) -> int:
        return 40 if v is None else v

    # NULL on rows created before the additive migration added the column.
    @field_validator("paper_finder_query", mode="before")
    @classmethod
    def _default_pf_query(cls, v: str | None) -> str:
        return "" if v is None else v

    @field_validator("paper_finder_min_score", mode="before")
    @classmethod
    def _default_pf_min_score(cls, v: float | None) -> float:
        return 0.0 if v is None else v


class ProjectPromptOut(BaseModel):
    """A customizable prompt for a project: registry metadata + the effective template."""

    key: str
    label: str
    stage: str
    channel: str
    contract_note: str
    placeholders: list[str]  # required placeholders that must remain
    placeholder_docs: dict[str, str]  # every placeholder → its meaning
    default_template: str
    template: str  # effective (the project's edit, else the default)
    is_custom: bool  # the project has edited this prompt


class ProjectPromptsUpdate(BaseModel):
    # Map of registry key -> full edited template. A value equal to the default (or
    # empty) resets that key; unknown keys are ignored; each template is validated.
    templates: dict[str, str]
