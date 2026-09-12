from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from alembic.config import Config
from app.core.config import settings
from app.db.session import get_db
from app.main import app

_ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


@pytest.fixture(scope="session", autouse=True)
def _migrated_schema() -> None:
    """Apply every Alembic migration to the test database once per test run.

    Per the testing decisions in issue #1, backend behavior is tested
    against a real Postgres database migrated through Alembic — never
    against SQLite or a mocked ORM.
    """
    alembic_config = Config(str(_ALEMBIC_INI))
    command.upgrade(alembic_config, "head")


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
    """A session bound to a per-test transaction, rolled back afterward.

    Keeps tests isolated from each other without needing to drop/recreate
    the schema between every test.
    """
    transaction = db_connection.begin()
    session = sessionmaker(bind=db_connection)()
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()


@pytest.fixture()
def client(db_session: Session) -> Generator[TestClient, None, None]:
    """A test client for the FastAPI app, hitting real routes end-to-end.

    The app's own `get_db` dependency is overridden to use the per-test
    transactional session above, so requests made through this client see
    (and roll back) the same data a test sets up directly via `db_session`.
    """
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()
