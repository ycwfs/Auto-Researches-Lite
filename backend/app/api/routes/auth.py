"""Identity route for the single-user edition: GET /auth/me returns the local user.

There is no register/login flow — every request acts as the auto-created local
user (see core/deps.get_current_user). The /auth/me URL is kept so the frontend
identity call stays stable.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.deps import get_current_user
from app.models.user import User
from app.schemas.auth import UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> User:
    return user
