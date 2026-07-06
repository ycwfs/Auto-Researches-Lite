"""Seed defaults: the single local user and paper sources."""
from __future__ import annotations

import os

from sqlalchemy.orm import Session

from app.models.admin import PaperSource
from app.models.user import User
from app.services.site_service import get_or_create_site_config

# The one local user every request acts as (no login, no tokens). hashed_password
# holds a fixed placeholder — '!' prefixed strings can never match a real hash.
LOCAL_USER_EMAIL = "local@auto-researches.local"
LOCAL_USER_NAME = "Local Researcher"
_LOCAL_PASSWORD_PLACEHOLDER = "!local"


def get_or_create_local_user(db: Session) -> User:
    """Fetch (or create) the single local user. Called during startup seeding AND
    lazily on every request (core/deps.get_current_user), so Project.owner_id /
    Job.user_id / Credential.user_id FKs always resolve even after a wiped DB."""
    user = db.query(User).filter(User.email == LOCAL_USER_EMAIL).one_or_none()
    if user is None:
        # Fall back to any pre-existing user (e.g. a DB migrated from the SaaS
        # build) so its projects/credentials stay owned and reachable.
        user = db.query(User).order_by(User.id).first()
    if user is None:
        user = User(
            email=LOCAL_USER_EMAIL,
            full_name=LOCAL_USER_NAME,
            hashed_password=_LOCAL_PASSWORD_PLACEHOLDER,
            is_admin=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    elif not user.is_admin or not user.is_active:
        user.is_admin = True
        user.is_active = True
        db.commit()
        db.refresh(user)
    return user

_DEFAULT_SOURCES = [
    ("arxiv", "arXiv", "Open-access preprints (cs.*, stat.ML, ...).", {}),
    (
        "semantic_scholar",
        "Semantic Scholar",
        "Cross-venue search via the S2 Graph API.",
        {"api_key_env": "S2_API_KEY"},
    ),
    (
        "ai_paper_finder",
        "AI Paper Finder",
        "Semantic search over a curated venue corpus via an HTTP endpoint "
        "(the paperfinder sidecar). Set PAPERFINDER_ENDPOINT or an admin endpoint.",
        # Endpoint also falls back to PAPERFINDER_ENDPOINT at fetch time, so an
        # already-seeded row still works once the env var is set.
        {"endpoint": os.environ.get("PAPERFINDER_ENDPOINT", "")},
    ),
]


def seed_defaults(db: Session) -> None:
    get_or_create_local_user(db)
    if db.query(PaperSource).count() == 0:
        for key, name, desc, config in _DEFAULT_SOURCES:
            db.add(
                PaperSource(
                    key=key, name=name, description=desc, enabled=True, config=config
                )
            )
    db.commit()
    # Ensure the site-config singleton exists so the public /site/config endpoint
    # and the runtime knobs (ideas floor, agent limits, worker concurrency) work
    # from the first request.
    get_or_create_site_config(db)
