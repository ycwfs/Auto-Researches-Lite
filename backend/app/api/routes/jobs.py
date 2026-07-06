"""Job status routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.enums import JobStatus
from app.models.job import Job
from app.models.user import User
from app.schemas.job import JobOut

router = APIRouter(prefix="/jobs", tags=["jobs"])

_TERMINAL = (JobStatus.succeeded, JobStatus.failed, JobStatus.canceled)


@router.get("/{job_id}", response_model=JobOut)
def get_job(
    job_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> Job:
    job = db.get(Job, job_id)
    if job is None or job.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return job


@router.post("/{job_id}/cancel", response_model=JobOut)
def cancel_job(
    job_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> Job:
    """Request cancellation of a job. A queued job is stopped immediately; a running
    job is flagged and stops at its next checkpoint (cooperative cancel). Already-
    finished jobs return 409. Ownership is row-level (404 for non-owners), so this one
    route covers every job type."""
    job = db.get(Job, job_id)
    if job is None or job.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    if job.status in _TERMINAL:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Job already finished")
    first_request = not job.cancel_requested  # avoid duplicate log lines on rapid re-clicks
    job.cancel_requested = True
    if job.status == JobStatus.queued:
        # Not yet started — no worker to cooperate, so stop it outright.
        job.status = JobStatus.canceled
        if first_request:
            job.log = (job.log or "") + "Canceled by user before it started.\n"
    elif first_request:
        job.log = (job.log or "") + "Cancellation requested — stopping at the next checkpoint.\n"
    db.commit()
    db.refresh(job)
    return job


@router.get("", response_model=list[JobOut])
def list_jobs(
    project_id: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[Job]:
    q = db.query(Job).filter(Job.user_id == user.id)
    if project_id is not None:
        q = q.filter(Job.project_id == project_id)
    return q.order_by(Job.created_at.desc()).limit(100).all()
