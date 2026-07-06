"""Curated .zip bundle of a project's source + context files.

The whole-project bundle (`build_project_zip`) ships every source/context artifact plus
the composed context document. Agent scratch (`.claude/`) and lockfiles are always
excluded; only human-readable sources (markdown/tex/bib/json/txt/style) are included.
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Callable

from sqlalchemy.orm import Session

from app.core.paths import project_dir
from app.models.project import Project
from app.services import context_service

# Human-readable source/context artifacts; binaries elsewhere are excluded.
_EXPORT_EXT = (".md", ".tex", ".bib", ".json", ".cls", ".sty", ".bst", ".txt")


def _member(rel_parts: tuple[str, ...], name: str) -> bool:
    """Whether a file under the project dir belongs in an export."""
    if ".claude" in rel_parts:  # agent scratch (skills, settings)
        return False
    if name.endswith(".lock"):
        return False
    # Human-readable sources only (discovery summaries, papers, full text).
    return name.lower().endswith(_EXPORT_EXT)


def _zip_tree(zf: zipfile.ZipFile, root: Path, keep: Callable[[tuple[str, ...]], bool]) -> None:
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if keep(rel.parts) and _member(rel.parts, path.name):
            zf.write(path, arcname=str(rel))


def build_project_zip(db: Session, project: Project) -> bytes:
    """All of the project's source/context files + the context document."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("context.md", context_service.render_context_md(db, project))
        _zip_tree(zf, project_dir(project.owner_id, project.id), lambda parts: True)
    return buf.getvalue()
