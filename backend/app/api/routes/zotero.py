"""Zotero integration routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.enums import JobType
from app.models.job import Job
from app.models.project import Project
from app.models.user import User
from app.schemas.job import JobOut
from app.schemas.zotero import (
    ZoteroCollection,
    ZoteroItem,
    ZoteroStatus,
    ZoteroUploadRequest,
)
from app.services import zotero_service
from app.workers.queue import find_inflight, submit

router = APIRouter(prefix="/zotero", tags=["zotero"])


@router.get("/status", response_model=ZoteroStatus)
def status_(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> ZoteroStatus:
    return ZoteroStatus(configured=zotero_service.is_configured(db, user))


@router.post("/validate")
def validate(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    return zotero_service.validate(db, user)


@router.get("/collections", response_model=list[ZoteroCollection])
def collections(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> list[dict]:
    return zotero_service.list_collections(db, user)


@router.get("/items", response_model=list[ZoteroItem])
def items(
    collection: str | None = None,
    limit: int = 50,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[dict]:
    return zotero_service.list_items(db, user, collection_key=collection, limit=min(limit, 100))


@router.post("/upload", response_model=JobOut, status_code=202)
def upload(
    payload: ZoteroUploadRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Job:
    """Sync selected papers (each with its Summary + code notes + a PDF link) and ideas
    to Zotero. Runs as a background job so it survives navigating away and uploads ALL
    selected papers (batched past Zotero's 50-per-request limit)."""
    project = db.get(Project, payload.project_id)
    if project is None or project.owner_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    if not zotero_service.is_configured(db, user):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Zotero is not connected. Add your API key, library ID and type in Settings.",
        )
    inflight = find_inflight(db, project.id, JobType.zotero_upload)
    if inflight is not None:
        return inflight  # a sync is already running for this project
    job = Job(
        project_id=project.id, user_id=user.id, type=JobType.zotero_upload,
        payload={
            "paper_ids": payload.paper_ids, "idea_ids": payload.idea_ids,
            "include_papers": payload.include_papers, "include_ideas": payload.include_ideas,
        },
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    submit("app.workers.tasks_zotero.run", job.id)
    return job
