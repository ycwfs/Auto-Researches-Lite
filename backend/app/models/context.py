"""ProjectContext and ChatMessage models — the project's evolving memory."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums import ChatRole


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ProjectContext(Base):
    """A per-project, stage-aware context document auto-maintained after each step.

    Each section is markdown, regenerated when its stage completes. Used as input
    for downstream steps and injected into the dialogue panel.
    """

    __tablename__ = "project_contexts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), unique=True, index=True
    )
    background: Mapped[str] = mapped_column(Text, default="")
    references: Mapped[str] = mapped_column(Text, default="")
    papers_summary: Mapped[str] = mapped_column(Text, default="")
    stage: Mapped[str] = mapped_column(String(20), default="discovery")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    project = relationship("Project", back_populates="context")


class EntityContext(Base):
    """An editable, per-entity context document for one idea or discovered paper.

    Discovery stays shared in `ProjectContext`; each idea (and each discovered paper on
    the Discover panel) gets its own context here. `content` is auto-composed from the
    entity + shared discovery (see context_service) and cached; once a user edits it,
    `is_custom` is set and the auto-rebuild leaves it alone (until an explicit regenerate).
    """

    __tablename__ = "entity_contexts"
    __table_args__ = (UniqueConstraint("scope", "scope_id", name="uq_entity_context_scope"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    scope: Mapped[str] = mapped_column(String(12), nullable=False)  # idea | discovered
    scope_id: Mapped[int] = mapped_column(Integer, nullable=False)  # the idea / discovered-paper id
    content: Mapped[str] = mapped_column(Text, default="")
    is_custom: Mapped[bool] = mapped_column(Boolean, default=False)  # user has hand-edited it
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class ChatMessage(Base):
    """A message in a project's dialogue panel.

    A thread is keyed by (project_id, scope, scope_id): `scope="project"` is the
    project-wide dialogue; `idea|discovered` + scope_id is a per-entity thread.
    """

    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    scope: Mapped[str] = mapped_column(String(12), default="project")  # project|idea|discovered
    scope_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    role: Mapped[ChatRole] = mapped_column(nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    project = relationship("Project", back_populates="messages")
