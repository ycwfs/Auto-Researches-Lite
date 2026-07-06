"""Project CRUD routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user, get_owned_project
from app.models.project import Project
from app.models.user import User
from app.schemas.discovery import PaperDocumentOut
from app.schemas.project import (
    ProjectCreate,
    ProjectOut,
    ProjectPromptOut,
    ProjectPromptsUpdate,
    ProjectUpdate,
)
from app.services import context_service, paper_db, prompts
from app.services.quota import check_can_create_project

router = APIRouter(prefix="/projects", tags=["projects"])

# Project fields that feed the context document's `background`.
_BACKGROUND_FIELDS = {"name", "description", "keywords", "target_venue"}


@router.get("", response_model=list[ProjectOut])
def list_projects(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> list[Project]:
    return (
        db.query(Project)
        .filter(Project.owner_id == user.id)
        .order_by(Project.updated_at.desc())
        .all()
    )


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
def create_project(
    payload: ProjectCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Project:
    check_can_create_project(db, user)
    project = Project(owner_id=user.id, **payload.model_dump())
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(project: Project = Depends(get_owned_project)) -> Project:
    return project


@router.get("/{project_id}/papers", response_model=list[PaperDocumentOut])
def explored_papers(
    project: Project = Depends(get_owned_project), db: Session = Depends(get_db)
) -> list[PaperDocumentOut]:
    """The project's explored papers (global PaperDocuments) with their Summaries — each
    resolved to this project's own override when set, else the shared default."""
    docs = paper_db.explored_for_project(db, project.id)
    ov = paper_db.overrides_map(db, project.id, [d.id for d in docs])
    out: list[PaperDocumentOut] = []
    for d in docs:
        v = paper_db._merge_view(ov.get(d.id), d)
        out.append(
            PaperDocumentOut(
                id=d.id,
                arxiv_id=d.arxiv_id or "",
                doi=d.doi or "",
                title=d.title,
                authors=list(d.authors or []),
                year=d.year or "",
                source=d.source or "",
                summary=v["summary"],
                extraction_method=d.extraction_method or "",
                has_markdown=bool(d.markdown),
                code_url=v["code_url"],
                code_summary=v["code_summary"],
                code_status=v["code_status"],
                created_at=d.created_at,
            )
        )
    return out


@router.patch("/{project_id}", response_model=ProjectOut)
def update_project(
    payload: ProjectUpdate,
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
) -> Project:
    changed = payload.model_dump(exclude_unset=True)
    for field, value in changed.items():
        setattr(project, field, value)
    db.commit()
    db.refresh(project)
    # Keep the context document's background in sync when metadata changes.
    if _BACKGROUND_FIELDS & changed.keys():
        try:
            context_service.refresh_background(db, project)
        except Exception:  # noqa: BLE001 — best-effort, never fail the project update
            pass
    return project


def _prompt_out(project: Project, spec) -> ProjectPromptOut:
    template = prompts.effective_template(project, spec.key)
    return ProjectPromptOut(
        key=spec.key, label=spec.label, stage=spec.stage, channel=spec.channel,
        contract_note=spec.contract_note,
        placeholders=list(spec.required_placeholders),
        placeholder_docs=dict(spec.placeholder_docs),
        default_template=spec.default_template,
        template=template,
        is_custom=template != spec.default_template,
    )


@router.get("/{project_id}/prompts", response_model=list[ProjectPromptOut])
def get_project_prompts(
    project: Project = Depends(get_owned_project),
) -> list[ProjectPromptOut]:
    """Each customizable prompt with this project's effective template (its edit or
    the built-in default), the required placeholders, and every placeholder's meaning."""
    return [_prompt_out(project, spec) for spec in prompts.REGISTRY.values()]


@router.put("/{project_id}/prompts", response_model=list[ProjectPromptOut])
def update_project_prompts(
    payload: ProjectPromptsUpdate,
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
) -> list[ProjectPromptOut]:
    """Save the project's edited prompt templates. Each template is validated (all
    required placeholders must remain); a template equal to the default (or empty)
    resets that key; unknown keys are ignored. A 422 lists every problem found."""
    overrides = dict(project.prompt_overrides or {})
    problems: list[str] = []
    for key, text in (payload.templates or {}).items():
        spec = prompts.REGISTRY.get(key)
        if spec is None:
            continue
        if not (text or "").strip() or text == spec.default_template:
            overrides.pop(key, None)  # reset to default
            continue
        key_problems = prompts.validate_template(key, text)
        if key_problems:
            problems.append(f"{spec.label}: " + "; ".join(key_problems))
            continue
        overrides[key] = text
    if problems:
        raise HTTPException(status_code=422, detail=" | ".join(problems))
    project.prompt_overrides = overrides
    db.commit()
    db.refresh(project)
    return [_prompt_out(project, spec) for spec in prompts.REGISTRY.values()]


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(
    project: Project = Depends(get_owned_project), db: Session = Depends(get_db)
) -> None:
    db.delete(project)
    db.commit()
