"""Guards ticket #60's fix: `conftest.py`'s `db_session` fixture must roll
back a row even when the test (or the service-layer code it calls, e.g.
`create_analysis_job`) commits from *inside* the test — not just when nothing
in the test ever calls `commit()` at all. A bare per-test transaction, rolled
back at teardown, has nothing left to undo if an internal `commit()` ever
ends it early; any row added before that commit would then be persisted for
real. The SAVEPOINT-join fix (`db_session`'s `begin_nested()` +
`after_transaction_end` listener) keeps an internal `commit()` from ever
reaching the outer transaction.

Self-contained rather than split across two tests relying on definition
order (a prior version of this file did that, and was caught in review:
running only the "did it leak" test in isolation — e.g. `pytest -k
test_previous_tests...` while debugging — passed trivially, since nothing
had committed a row yet, giving false confidence). Instead, this drives the
actual `db_session` fixture's generator manually (via `__wrapped__`, since
pytest fixture functions can't be called directly otherwise) so its
teardown — the real regression-guarded behavior — runs *inside* this one
test, before asserting the post-teardown database state through a
completely independent connection.
"""

from sqlalchemy import create_engine, select

from app.core.config import settings
from app.models.account import Account, AccountRole
from tests.conftest import db_session as _db_session_fixture

_LEAK_CHECK_EMAIL = "ticket-60-leak-check@vives.be"


def test_a_session_commit_inside_a_test_does_not_leak_past_teardown(db_connection):
    generator = _db_session_fixture.__wrapped__(db_connection)
    session = next(generator)

    session.add(
        Account(
            email=_LEAK_CHECK_EMAIL,
            password_hash=None,
            display_name="Leak Check",
            role=AccountRole.USER,
            is_active=True,
        )
    )
    # Mirrors every real service-layer function (create_analysis_job,
    # claim_next_queued_job, ...): commits from inside the test, rather than
    # leaving nothing committed for the fixture's own rollback to undo.
    session.commit()

    # Drive the fixture's own teardown (session.close() + transaction.rollback())
    # right here, rather than waiting for pytest to run it once this test
    # function returns.
    next(generator, None)

    engine = create_engine(settings.database_url)
    try:
        with engine.connect() as verification_connection:
            leaked = verification_connection.execute(
                select(Account.id).where(Account.email == _LEAK_CHECK_EMAIL)
            ).first()
    finally:
        engine.dispose()

    assert leaked is None
