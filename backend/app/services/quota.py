"""No-op quota guards (open-source single-user edition).

The SaaS build metered usage per subscription tier here. The OSS edition has no
tiers and no billing, so every guard is an always-allow no-op. The public names
are preserved so call sites (routes, scheduler) stay untouched: `check_can_*`
raising guards simply return, and the `can_*` predicates return True.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.user import User

__all__ = [
    "check_can_create_project",
    "check_can_run_discovery",
    "check_can_add_paper",
    "check_can_resummarize",
    "check_can_run_ideas",
    "check_can_rank_baseline",
    "check_can_start_experiment",
    "check_can_generate_brief",
    "check_can_create_draft",
    "check_can_chat",
    "check_can_generate_figure",
    "check_can_use_ssh",
    "can_run_discovery",
    "can_run_ideas",
]


# --------------------------------------------------------------------------- #
# Raising guards (HTTP routes) — always allow
# --------------------------------------------------------------------------- #
def check_can_create_project(db: Session, user: User) -> None:
    return None


def check_can_run_discovery(db: Session, user: User) -> None:
    return None


def check_can_add_paper(db: Session, user: User) -> None:
    return None


def check_can_resummarize(db: Session, user: User) -> None:
    return None


def check_can_run_ideas(db: Session, user: User) -> None:
    return None


def check_can_rank_baseline(db: Session, user: User) -> None:
    return None


def check_can_start_experiment(db: Session, user: User) -> None:
    return None


def check_can_generate_brief(db: Session, user: User) -> None:
    return None


def check_can_create_draft(db: Session, user: User) -> None:
    return None


def check_can_chat(db: Session, user: User) -> None:
    return None


def check_can_generate_figure(db: Session, user: User) -> None:
    return None


def check_can_use_ssh(db: Session, user: User) -> None:
    return None


# --------------------------------------------------------------------------- #
# Non-raising predicates (background scheduler path) — always allow
# --------------------------------------------------------------------------- #
def can_run_discovery(db: Session, user: User) -> bool:
    return True


def can_run_ideas(db: Session, user: User) -> bool:
    return True
