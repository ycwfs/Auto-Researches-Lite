"""Pytest fixtures: isolated SQLite DB and a single-user TestClient.

Single-user edition: there is no register/login flow. Every request acts as the
auto-created local user (see core/deps.get_current_user). The historical
`auth_client` and `admin_client` fixture names survive here as aliases of one
unauthenticated TestClient so the existing test files run unchanged.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

# Configure an isolated environment BEFORE importing app modules.
_TMP = Path(tempfile.mkdtemp(prefix="far-test-"))
os.environ["FAR_ENV_FILE"] = str(_TMP / "nonexistent.env")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP / 'test.db'}"
os.environ["DATA_ROOT"] = str(_TMP / "data")
os.environ["OFFLINE_MODE"] = "true"
os.environ["JOB_SYNC"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.core.database import init_db  # noqa: E402
from app.main import app  # noqa: E402


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "network: test hits a live external network service"
    )


@pytest.fixture(scope="session", autouse=True)
def _db() -> None:
    init_db()


@pytest.fixture(autouse=True)
def _reset_db() -> None:
    """Restore a clean single-user baseline before every test.

    The SaaS suite isolated per-user data by registering a fresh user per test.
    The single-user edition has exactly one local user shared by all requests, so
    per-user state (credentials, projects, skills, jobs, ideas, ...) would leak
    across tests. Wipe every table and reseed the baseline (local user + default
    paper sources + site-config singleton) so each test starts clean, matching the
    old fresh-user isolation. All state is DB-backed (no in-process caches)."""
    from app.core.database import Base, SessionLocal, engine
    from app.services.seed import seed_defaults

    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())

    db = SessionLocal()
    try:
        seed_defaults(db)
    finally:
        db.close()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def auth_client(client: TestClient) -> TestClient:
    """Single-user client. No auth is required — every request is the local user."""
    return client


@pytest.fixture
def admin_client() -> TestClient:
    """Alias of the single-user client. The local user is the admin."""
    return TestClient(app)
