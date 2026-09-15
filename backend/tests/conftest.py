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
from app.services.mail import get_mail_transport
from app.services.rate_limit import RateLimiter, get_rate_limiter
from tests.fakes import FakeMailTransport

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
def mail_transport() -> FakeMailTransport:
    """The fake mail transport injected into the app for this test — see
    tests/fakes.py and issue #1's testing decisions."""
    return FakeMailTransport()


@pytest.fixture()
def rate_limiter() -> RateLimiter:
    """A fresh RateLimiter injected per test.

    The real one (get_rate_limiter's module-level singleton) is a
    process-lifetime object by design — its state has to persist across
    requests to mean anything — which would otherwise leak attempt counts
    between tests within the same pytest run. Same reasoning as
    mail_transport above.
    """
    return RateLimiter()


@pytest.fixture()
def client(
    db_session: Session, mail_transport: FakeMailTransport, rate_limiter: RateLimiter
) -> Generator[TestClient, None, None]:
    """A test client for the FastAPI app, hitting real routes end-to-end.

    The app's own `get_db`/`get_mail_transport`/`get_rate_limiter`
    dependencies are overridden to use the per-test transactional session,
    fake mail transport, and fresh rate limiter above, so requests made
    through this client see (and roll back) the same data a test sets up
    directly, never send real email, and start with a clean rate-limit
    slate every test.
    """
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_mail_transport] = lambda: mail_transport
    app.dependency_overrides[get_rate_limiter] = lambda: rate_limiter
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()
