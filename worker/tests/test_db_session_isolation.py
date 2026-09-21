"""Guards ticket #60's fix in worker/tests/conftest.py — same rationale as
backend/tests/test_db_session_isolation.py, exercised here against
`create_analysis_job` (which commits internally), matching
how worker/tests actually drives `db_session` through real service-layer
calls rather than raw ORM adds.

Self-contained rather than split across two order-dependent tests — see
backend/tests/test_db_session_isolation.py's docstring for why (caught in
review: a test relying on a previous test's fixture teardown having already
run passes trivially when run alone). This drives the actual `db_session`
fixture's generator manually (via `__wrapped__`) so its teardown runs
inside this one test, before checking the post-teardown database state
through a completely independent connection.
"""

from app.core.config import settings
from app.models.analysis_job import AnalysisJob
from app.services.analyses import create_analysis_job
from sqlalchemy import create_engine, select

from tests.conftest import db_session as _db_session_fixture

_LEAK_CHECK_TEST_ID = "T999"


def test_create_analysis_job_inside_a_test_does_not_leak_past_teardown(db_connection):
    generator = _db_session_fixture.__wrapped__(db_connection)
    session = next(generator)

    create_analysis_job(
        session,
        requested_by_identity=None,
        test_id=_LEAK_CHECK_TEST_ID,
        cut_keys=[f"cuts/{_LEAK_CHECK_TEST_ID}/{_LEAK_CHECK_TEST_ID}_C2_ME_F1.mp4"],
    )

    # Drive the fixture's own teardown (session.close() + transaction.rollback())
    # right here, rather than waiting for pytest to run it once this test
    # function returns.
    next(generator, None)

    engine = create_engine(settings.database_url)
    try:
        with engine.connect() as verification_connection:
            leaked = verification_connection.execute(
                select(AnalysisJob.id).where(AnalysisJob.test_id == _LEAK_CHECK_TEST_ID)
            ).first()
    finally:
        engine.dispose()

    assert leaked is None
