"""SQLAlchemy models. Importing this package registers all tables (and the per-entity
context/chat cleanup listeners below)."""
from sqlalchemy import delete, event

from app.models.admin import (
    IntegrationConfig,
    ModelCatalog,
    PaperSource,
    SiteConfig,
)
from app.models.content import Paper, PaperDocument, ProjectDocumentRef
from app.models.context import ChatMessage, EntityContext, ProjectContext
from app.models.job import Job
from app.models.project import Project
from app.models.user import Credential, User


def _purge_entity_context(scope: str):
    """before_delete handler: drop the deleted entity's per-entity context + chat thread.

    `EntityContext.scope_id` / `ChatMessage.scope_id` are polymorphic (no FK), so nothing
    cascades them automatically — this fires on any ORM delete of the entity (a future
    per-entity delete route, or the ORM cascade when its project is deleted)."""

    def _handler(_mapper, connection, target) -> None:
        connection.execute(
            delete(EntityContext).where(
                EntityContext.scope == scope, EntityContext.scope_id == target.id
            )
        )
        connection.execute(
            delete(ChatMessage).where(
                ChatMessage.scope == scope, ChatMessage.scope_id == target.id
            )
        )

    return _handler


# Discovered papers carry their own chat/context too. NOTE: the bulk
# POST /papers/delete route bypasses ORM events — it purges these rows itself.
event.listen(Paper, "before_delete", _purge_entity_context("discovered"))

__all__ = [
    "User",
    "Credential",
    "Project",
    "Paper",
    "PaperDocument",
    "ProjectDocumentRef",
    "Job",
    "PaperSource",
    "ModelCatalog",
    "SiteConfig",
    "IntegrationConfig",
    "ProjectContext",
    "EntityContext",
    "ChatMessage",
]
