"""Project dialogue panel routes — project-wide and per-entity (idea / discovered paper)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user, get_owned_project
from app.models.project import Project
from app.models.user import User
from app.schemas.context import ChatMessageOut, ChatRequest
from app.services import chat_service, context_service
from app.services.quota import check_can_chat

router = APIRouter(prefix="/projects/{project_id}/chat", tags=["chat"])


def _validate_scope(db: Session, project: Project, scope: str, scope_id: int | None) -> None:
    """Entity-scoped chat must reference an entity that belongs to this project."""
    if scope == "project":
        return
    if scope not in context_service.ENTITY_SCOPES or not scope_id:
        raise HTTPException(status_code=400, detail="Invalid chat scope")
    entity = context_service.entity_in_project(db, project, scope, scope_id)
    if entity is None:
        raise HTTPException(status_code=404, detail=f"{scope} not found")
    # A discovered-paper chat only makes sense once the paper has parsed full text
    # to ground answers in (not just the abstract).
    if scope == "discovered" and not context_service.discovered_paper_has_fulltext(db, entity):
        raise HTTPException(
            status_code=409,
            detail="This paper has no parsed full text yet — open its Summary to build it first.",
        )


@router.get("", response_model=list[ChatMessageOut])
def get_history(
    scope: str = "project",
    scope_id: int | None = None,
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
) -> list:
    _validate_scope(db, project, scope, scope_id)
    return chat_service.history(db, project, scope, scope_id)


@router.post("", response_model=ChatMessageOut)
def post_message(
    payload: ChatRequest,
    scope: str = "project",
    scope_id: int | None = None,
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ChatMessageOut:
    _validate_scope(db, project, scope, scope_id)
    check_can_chat(db, user)
    return chat_service.reply(db, project, user, payload.message, scope, scope_id)
