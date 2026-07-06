"""SQLAlchemy engine, session factory, and declarative base."""
from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings


class Base(DeclarativeBase):
    pass


def _engine_kwargs(url: str) -> dict:
    if url.startswith("sqlite"):
        # SQLite needs this flag for use across FastAPI threads / RQ workers.
        return {"connect_args": {"check_same_thread": False}}
    return {"pool_pre_ping": True}


engine = create_engine(settings.database_url, **_engine_kwargs(settings.database_url))
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a scoped DB session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create all tables, then add any missing columns / native-enum values."""
    from app import models  # noqa: F401  (side-effect: registers models)

    Base.metadata.create_all(bind=engine)
    _ensure_columns()
    _ensure_enum_values()
    _widen_site_config_favicon()
    _ensure_paper_doc_indexes()


def _ensure_columns() -> None:
    """Lightweight additive migration: ADD COLUMN for model columns missing in
    pre-existing tables (handles schema growth without a migration tool).

    New columns are added nullable; application code tolerates NULL via defaults.
    """
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue  # freshly created by create_all
            db_cols = {c["name"] for c in inspector.get_columns(table.name)}
            for col in table.columns:
                if col.name in db_cols:
                    continue
                col_type = col.type.compile(dialect=engine.dialect)
                conn.execute(
                    text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {col_type}')
                )


def _ensure_enum_values() -> None:
    """Postgres only: ALTER TYPE ... ADD VALUE for native-enum labels added to the
    Python enums after the type was first created. (SQLite stores enums as text, so
    new values just work there — this keeps Postgres deployments in sync without a
    migration tool. Idempotent.)
    """
    if not engine.dialect.name.startswith("postgres"):
        return
    from sqlalchemy import Enum as SAEnum, text

    wanted: dict[str, set[str]] = {}
    for table in Base.metadata.sorted_tables:
        for col in table.columns:
            if isinstance(col.type, SAEnum) and col.type.name:
                wanted.setdefault(col.type.name, set()).update(col.type.enums)

    # AUTOCOMMIT: ALTER TYPE ... ADD VALUE must not run inside a transaction block.
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        for type_name, values in wanted.items():
            existing = {
                row[0]
                for row in conn.execute(
                    text(
                        "SELECT e.enumlabel FROM pg_enum e "
                        "JOIN pg_type t ON t.oid = e.enumtypid WHERE t.typname = :n"
                    ),
                    {"n": type_name},
                )
            }
            if not existing:
                continue  # type not created yet (create_all will make it complete)
            for value in values - existing:
                conn.execute(text(f"ALTER TYPE \"{type_name}\" ADD VALUE IF NOT EXISTS '{value}'"))


def _ensure_paper_doc_indexes() -> None:
    """Best-effort: add the partial unique indexes to an already-existing
    paper_documents table (create_all only constrains freshly-created tables, so a
    pre-existing table would otherwise lack them and dedup couldn't converge on a
    race). Each runs in its own transaction; idempotent; skipped if it can't apply
    (e.g. the table already holds colliding duplicate rows)."""
    import logging

    from sqlalchemy import text

    logger = logging.getLogger("far.db")
    for name, col in (("uq_pd_arxiv", "arxiv_id"), ("uq_pd_doi", "doi"), ("uq_pd_title", "title_key")):
        try:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        f"CREATE UNIQUE INDEX IF NOT EXISTS {name} "
                        f"ON paper_documents ({col}) WHERE {col} != ''"
                    )
                )
        except Exception as exc:  # pragma: no cover - defensive startup guard
            logger.warning("paper_documents index %s skipped: %s", name, exc)


def _widen_site_config_favicon() -> None:
    """Postgres: widen site_config.favicon_url to TEXT so it can hold an uploaded
    favicon as a base64 data URI (the column was first created as VARCHAR(500)).
    SQLite uses TEXT affinity already, so this is a no-op there. Idempotent."""
    if not engine.dialect.name.startswith("postgres"):
        return
    import logging

    from sqlalchemy import text

    logger = logging.getLogger("far.db")
    try:
        with engine.begin() as conn:
            row = conn.execute(
                text(
                    "SELECT data_type FROM information_schema.columns "
                    "WHERE table_name = 'site_config' AND column_name = 'favicon_url'"
                )
            ).fetchone()
            if row and row[0] != "text":
                conn.execute(text("ALTER TABLE site_config ALTER COLUMN favicon_url TYPE TEXT"))
    except Exception as exc:  # pragma: no cover - defensive startup guard
        logger.warning("favicon_url widen skipped: %s", exc)
