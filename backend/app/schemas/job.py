"""Job schemas."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator

from app.models.enums import JobStatus, JobType


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    type: JobType
    status: JobStatus
    progress: int
    log: str
    error: str
    cancel_requested: bool = False  # a cancel was requested; a running job stops at its next checkpoint
    target_id: int | None
    payload: dict = {}  # job-specific inputs; no secrets
    created_at: datetime
    updated_at: datetime

    # `payload` is NULL on rows created before the column existed.
    @field_validator("payload", mode="before")
    @classmethod
    def _none_to_dict(cls, v: dict | None) -> dict:
        return v or {}

    # `cancel_requested` is NULL on rows created before the column existed.
    @field_validator("cancel_requested", mode="before")
    @classmethod
    def _none_to_false(cls, v: bool | None) -> bool:
        return bool(v)
