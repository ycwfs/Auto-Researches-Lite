"""Per-user / per-project artifact directories."""
from __future__ import annotations

from pathlib import Path

from app.core.config import settings


def project_dir(user_id: int, project_id: int) -> Path:
    path = settings.data_dir / f"u{user_id}" / f"p{project_id}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def discovery_dir(user_id: int, project_id: int) -> Path:
    path = project_dir(user_id, project_id) / "discovery"
    path.mkdir(parents=True, exist_ok=True)
    return path


def uploads_dir(user_id: int, project_id: int) -> Path:
    """Raw user-uploaded artifacts (e.g. manually added paper PDFs) for a project."""
    path = discovery_dir(user_id, project_id) / "uploads"
    path.mkdir(parents=True, exist_ok=True)
    return path
