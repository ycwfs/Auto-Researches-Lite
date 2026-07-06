"""Project context and chat schemas."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ProjectContextOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    background: str
    references: str
    papers_summary: str
    stage: str
    updated_at: datetime | None = None


class ProjectContextUpdate(BaseModel):
    background: str | None = None
    references: str | None = None
    papers_summary: str | None = None


class ScopedContextOut(BaseModel):
    """A per-entity (idea / discovered paper) context document."""

    model_config = ConfigDict(from_attributes=True)
    scope: str
    scope_id: int
    content: str
    is_custom: bool
    updated_at: datetime | None = None


class ScopedContextUpdate(BaseModel):
    content: str


class ChatMessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    role: str
    content: str
    created_at: datetime


class ChatRequest(BaseModel):
    message: str
