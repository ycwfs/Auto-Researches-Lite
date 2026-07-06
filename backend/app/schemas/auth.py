"""User schemas (single-user edition)."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    # Plain str (not EmailStr): the local user's fixed address uses the reserved
    # .local TLD, which strict email validation rejects.
    email: str
    full_name: str
    is_admin: bool
    created_at: datetime
