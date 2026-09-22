from collections.abc import Generator, Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.orm import Session, SessionTransaction, sessionmaker

from alembic import command
from alembic.config import Config
from app.core.config import settings
from app.db.session import get_db
from app.main import app
from app.services.cutting_uploads import get_cutting_upload_root
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
def scratch_database(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """A fresh, empty database, with `settings.database_url` pointed at it
    for the test's duration (alembic/env.py reads the URL from settings).

    For a migration test that needs to control exactly which revisions are
    applied (ticket #72's `test_migration_remove_application_auth.py`,
    ticket #82's `test_migration_audit_log.py`) — the shared, already-
    head-migrated schema `_migrated_schema`/`db_session` use can't be
    reused for that, since it's already past the revisions being tested.
    """
    base_url = make_url(settings.database_url)
    # A generated hex name, never user input — DDL can't take a bound
    # parameter for an identifier.
    name = f"migration_test_{uuid4().hex[:12]}"
    admin_engine = create_engine(base_url, isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as admin:
            admin.execute(text(f'CREATE DATABASE "{name}"'))
        scratch_url = base_url.set(database=name).render_as_string(hide_password=False)
        monkeypatch.setattr(settings, "database_url", scratch_url)
        yield scratch_url
    finally:
        with admin_engine.connect() as admin:
            admin.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin_engine.dispose()


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
def cutting_upload_root(tmp_path: Path) -> Path:
    """Ticket #94: the scoped local temp directory cutting-job uploads land
    in for this test, injected in place of `settings.cutting_upload_temp_dir`
    — never a real shared directory, same "point the seam at tmp_path"
    approach as `s3_client`/`media_prober` above use fakes for."""
    return tmp_path / "cutting-uploads"


@pytest.fixture()
def client(
    db_session: Session,
    rate_limiter: RateLimiter,
    s3_client: FakeS3Client,
    media_prober: FakeMediaProber,
    cutting_upload_root: Path,
) -> Generator[TestClient, None, None]:
    """A test client for the FastAPI app, hitting real routes end-to-end.

    The app's own `get_db`/`get_rate_limiter`/`get_s3_client`/
    `get_media_prober`/`get_cutting_upload_root` dependencies are
    overridden to use the per-test transactional session, fresh rate
    limiter, fake S3 client, fake media prober, and scoped tmp_path above,
    so requests made through this client see (and roll back) the same data
    a test sets up directly, never touch the real bucket or run the real
    `ffprobe` binary, never write outside pytest's own tmp_path, and start
    with a clean rate-limit slate every test.
    """
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_rate_limiter] = lambda: rate_limiter
    app.dependency_overrides[get_s3_client] = lambda: s3_client
    app.dependency_overrides[get_media_prober] = lambda: media_prober
    app.dependency_overrides[get_cutting_upload_root] = lambda: cutting_upload_root
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()
