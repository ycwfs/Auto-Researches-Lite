"""Project dialogue panel — context-grounded chat with the LLM (mock offline)."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.context import ChatMessage
from app.models.enums import ChatRole
from app.models.project import Project
from app.models.user import User
from app.services import context_service
from app.services.model_select import build_llm_for_step


def _scoped(query, scope: str, scope_id: int | None):
    """Filter a ChatMessage query to one thread. The project thread also matches legacy
    rows where `scope` is NULL (added by the additive migration)."""
    if scope == "project" or not scope_id:
        return query.filter(
            ChatMessage.scope_id.is_(None),
            (ChatMessage.scope == "project") | (ChatMessage.scope.is_(None)),
        )
    return query.filter(ChatMessage.scope == scope, ChatMessage.scope_id == scope_id)


def history(
    db: Session,
    project: Project,
    scope: str = "project",
    scope_id: int | None = None,
    limit: int = 50,
) -> list[ChatMessage]:
    q = db.query(ChatMessage).filter(ChatMessage.project_id == project.id)
    return _scoped(q, scope, scope_id).order_by(ChatMessage.id.asc()).limit(limit).all()


def reply(
    db: Session,
    project: Project,
    user: User,
    message: str,
    scope: str = "project",
    scope_id: int | None = None,
) -> ChatMessage:
    sid = scope_id if scope != "project" else None
    db.add(
        ChatMessage(
            project_id=project.id, scope=scope, scope_id=sid, role=ChatRole.user, content=message
        )
    )
    db.flush()

    context = context_service.context_for_scope(db, project, scope, sid)
    prior = [
        (m.role.value, m.content)
        for m in history(db, project, scope, sid)
        if m.role != ChatRole.system
    ]
    llm = build_llm_for_step(db, user.id, project, "chat")
    answer = llm.chat(context, prior, message)

    assistant = ChatMessage(
        project_id=project.id, scope=scope, scope_id=sid, role=ChatRole.assistant, content=answer
    )
    db.add(assistant)
    db.commit()
    db.refresh(assistant)
    return assistant
