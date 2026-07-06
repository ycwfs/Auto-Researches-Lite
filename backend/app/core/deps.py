"""FastAPI dependencies: single-user resolution and project lookup.

Open-source single-user edition: there is no login and no tokens. Every request
acts as the one auto-created local user (see services/seed.get_or_create_local_user).
This module stays the single auth choke point — every router imports
get_current_user / get_owned_project / require_admin from here, so the whole API
runs single-user with zero router edits.
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.project import Project
from app.models.user import User


def get_current_user(db: Session = Depends(get_db)) -> User:
    """Return the local single user, creating it lazily if the DB was wiped."""
    from app.services.seed import get_or_create_local_user

    return get_or_create_local_user(db)


def get_current_user_flexible(db: Session = Depends(get_db)) -> User:
    """Same as get_current_user. Kept as a distinct dependency for browser-initiated
    GETs (img src, download links) that historically passed a `token` query param;
    those URLs are now unauthenticated."""
    from app.services.seed import get_or_create_local_user

    return get_or_create_local_user(db)


def get_owned_project_flexible(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_flexible),
) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project


def get_owned_project(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project


def require_admin(user: User = Depends(get_current_user)) -> User:
    """Single-user mode: the local user is always the admin."""
    return user
