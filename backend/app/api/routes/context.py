"""Project context routes — the shared project context + per-entity contexts."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_owned_project, get_owned_project_flexible
from app.models.project import Project
from app.schemas.context import (
    ProjectContextOut,
    ProjectContextUpdate,
    ScopedContextOut,
    ScopedContextUpdate,
)
from app.services import context_service, export_service

router = APIRouter(prefix="/projects/{project_id}", tags=["context"])


@router.get("/export.zip")
def export_project(
    project: Project = Depends(get_owned_project_flexible), db: Session = Depends(get_db)
) -> Response:
    """Download all of the project's source/context files as a curated .zip: the context
    document (context.md) plus discovery summaries, ideas, and source full-text — no agent
    scratch or lockfiles."""
    return Response(
        content=export_service.build_project_zip(db, project),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="project-{project.id}-sources.zip"'},
    )


# ---- Project (shared discovery + overview) context -------------------------
@router.get("/context", response_model=ProjectContextOut)
def get_context(
    project: Project = Depends(get_owned_project), db: Session = Depends(get_db)
) -> ProjectContextOut:
    ctx = context_service.get_or_create(db, project)
    db.commit()
    return ctx


@router.put("/context", response_model=ProjectContextOut)
def update_context(
    payload: ProjectContextUpdate,
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
) -> ProjectContextOut:
    ctx = context_service.get_or_create(db, project)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(ctx, field, value)
    db.commit()
    db.refresh(ctx)
    return ctx


# ---- Per-entity (idea / discovered paper) context --------------------------
def _owned_entity(db: Session, project: Project, scope: str, entity_id: int):
    if scope not in context_service.ENTITY_SCOPES:
        raise HTTPException(status_code=404, detail="Unknown context scope")
    entity = context_service.entity_in_project(db, project, scope, entity_id)
    if entity is None:
        raise HTTPException(status_code=404, detail=f"{scope} not found")
    return entity


@router.get("/entity-context/{scope}/{entity_id}", response_model=ScopedContextOut)
def get_entity_context(
    scope: str,
    entity_id: int,
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
) -> ScopedContextOut:
    entity = _owned_entity(db, project, scope, entity_id)
    return context_service.resolve_entity_context(db, project, scope, entity)


@router.put("/entity-context/{scope}/{entity_id}", response_model=ScopedContextOut)
def put_entity_context(
    scope: str,
    entity_id: int,
    payload: ScopedContextUpdate,
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
) -> ScopedContextOut:
    entity = _owned_entity(db, project, scope, entity_id)
    return context_service.set_entity_context(db, project, scope, entity, payload.content)


@router.post("/entity-context/{scope}/{entity_id}/regenerate", response_model=ScopedContextOut)
def regenerate_entity_context(
    scope: str,
    entity_id: int,
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
) -> ScopedContextOut:
    entity = _owned_entity(db, project, scope, entity_id)
    return context_service.regenerate_entity_context(db, project, scope, entity)
