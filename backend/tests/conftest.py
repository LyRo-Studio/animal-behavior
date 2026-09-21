from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session, SessionTransaction, sessionmaker

from alembic import command
from alembic.config import Config
from app.core.config import settings
from app.db.session import get_db
from app.main import app
from app.services.media_prober import get_media_prober
from app.services.rate_limit import RateLimiter, get_rate_limiter
from app.services.s3_client import get_s3_client
from tests.fakes import FakeMediaProber, FakeS3Client
from tests.helpers import IDENTITY_HEADER

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

    Most service-layer functions (`create_analysis_job`, `claim_next_queued_job`,
    `finalize_analysis_job`, ...) call `session.commit()`
    themselves. A session bound directly to `db_connection` with nothing
    else in play would let that internal commit end the outer `transaction`
    early, leaving the `transaction.rollback()` below with nothing left to
    undo (ticket #60). SQLAlchemy's own documented fix for "joining a
    session into an external transaction" is used instead: start a
    SAVEPOINT (`begin_nested`) and restart it every time it ends, via the
    `after_transaction_end` event — so an internal `commit()` only ends the
    SAVEPOINT, never `transaction` itself, which the `finally` block below
    can then always roll back for real.
    """
    transaction = db_connection.begin()
    session = sessionmaker(bind=db_connection)()
    session.begin_nested()

    @event.listens_for(session, "after_transaction_end")
    def _restart_savepoint(session: Session, session_transaction: SessionTransaction) -> None:
        if session_transaction.nested and not session_transaction._parent.nested:
            session.expire_all()
            session.begin_nested()

    try:
        yield session
    finally:
        event.remove(session, "after_transaction_end", _restart_savepoint)
        session.close()
        transaction.rollback()


@pytest.fixture(autouse=True)
def _identity_header_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point `settings.identity_header_name` at `tests.helpers.IDENTITY_HEADER`
    for every test (ticket #72) — production reads it from `.env`, and a
    test run with no `.env` would otherwise never read any identity at all.
    Tests that want it unconfigured monkeypatch it back to None themselves.
    """
    monkeypatch.setattr(settings, "identity_header_name", IDENTITY_HEADER)


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
def s3_client() -> FakeS3Client:
    """The fake S3 client injected into the app for this test (ticket #18)
    — see tests/fakes.py. No test in this ticket touches the real bucket."""
    return FakeS3Client()


@pytest.fixture()
def media_prober() -> FakeMediaProber:
    """The fake media prober injected into the app for this test (ticket
    #22) — see tests/fakes.py. No test runs the real `ffprobe` binary or
    needs real video bytes."""
    return FakeMediaProber()


@pytest.fixture()
def client(
    db_session: Session,
    rate_limiter: RateLimiter,
    s3_client: FakeS3Client,
    media_prober: FakeMediaProber,
) -> Generator[TestClient, None, None]:
    """A test client for the FastAPI app, hitting real routes end-to-end.

    The app's own `get_db`/`get_rate_limiter`/`get_s3_client`/
    `get_media_prober` dependencies are overridden to use the per-test
    transactional session, fresh rate limiter, fake S3 client, and fake
    media prober above, so requests made through this client see (and roll
    back) the same data a test sets up directly, never touch the real
    bucket or run the real `ffprobe` binary, and start with a clean
    rate-limit slate every test.
    """
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_rate_limiter] = lambda: rate_limiter
    app.dependency_overrides[get_s3_client] = lambda: s3_client
    app.dependency_overrides[get_media_prober] = lambda: media_prober
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()
