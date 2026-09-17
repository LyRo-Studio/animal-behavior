import os
from collections.abc import Generator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from app.core.config import settings
from sqlalchemy import create_engine
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session, sessionmaker

# Same real-Postgres, real-Alembic-migrations testing convention as
# backend/tests/conftest.py (issue #1's testing decisions) — the worker
# talks to the exact same schema, so its tests are held to the same bar.
_BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"
_ALEMBIC_INI = _BACKEND_DIR / "alembic.ini"


@pytest.fixture(scope="session", autouse=True)
def _migrated_schema() -> None:
    """Apply every Alembic migration to the test database once per test run.

    backend/alembic.ini's `script_location = alembic` is a path relative to
    the current working directory, not to the ini file's own location, so
    this temporarily chdirs into backend/ for the upgrade call only —
    running worker's own tests from worker/ would otherwise look for a
    (nonexistent) worker/alembic.
    """
    alembic_config = Config(str(_ALEMBIC_INI))
    original_cwd = Path.cwd()
    os.chdir(_BACKEND_DIR)
    try:
        command.upgrade(alembic_config, "head")
    finally:
        os.chdir(original_cwd)


@pytest.fixture()
def db_connection() -> Generator[Connection, None, None]:
    engine = create_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            yield connection
    finally:
        engine.dispose()


@pytest.fixture()
def db_session(db_connection: Connection) -> Generator[Session, None, None]:
    """A session bound to a per-test transaction, rolled back afterward —
    keeps tests isolated without dropping/recreating the schema between
    every test, same as backend/tests/conftest.py's identical fixture.
    """
    transaction = db_connection.begin()
    session = sessionmaker(bind=db_connection)()
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()


@pytest.fixture()
def work_root(tmp_path: Path) -> Path:
    """A fresh, empty job-temp-file root per test — stands in for
    settings.analysis_worker_work_dir."""
    return tmp_path / "work"
