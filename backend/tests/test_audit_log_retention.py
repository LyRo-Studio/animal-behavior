"""Direct tests of `prune_old_audit_events` (ticket #87, issue #79's
retention policy) — same shape as `test_audit_log.py`'s direct tests of
`record_audit_event`, against a real (Alembic-migrated) test DB.

Each test clears `audit_log` first: this suite runs against a real
Postgres database that, outside CI, may be a long-lived shared instance
also written to by other processes (a running backend/worker), not a
pristine one scoped to this test alone. The clear is safe — `db_session`
joins its own per-test SAVEPOINT (CONTEXT.md's "Test DB isolation"
decision), so it's rolled back at teardown along with everything else this
test does, never actually deleting another process's rows for real.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select

from app.models.audit_log import AuditAction, AuditLog
from app.services.audit_log import prune_old_audit_events

_RETENTION_DAYS = 90


def _clear_audit_log(db_session) -> None:
    db_session.execute(delete(AuditLog))
    db_session.commit()


def _insert_row(db_session, *, occurred_at: datetime, target: str) -> None:
    db_session.add(
        AuditLog(
            occurred_at=occurred_at,
            identity="jan.peeters@vives.be",
            identity_verified=False,
            action=AuditAction.ANALYSIS_STARTED,
            target_type="analysis_job",
            target=target,
        )
    )
    db_session.commit()


def test_prune_old_audit_events_deletes_rows_past_the_retention_window(db_session):
    _clear_audit_log(db_session)
    now = datetime.now(UTC)
    _insert_row(db_session, occurred_at=now - timedelta(days=_RETENTION_DAYS + 1), target="old")
    _insert_row(db_session, occurred_at=now - timedelta(days=1), target="recent")

    deleted = prune_old_audit_events(db_session, retention_days=_RETENTION_DAYS)

    assert deleted == 1
    remaining = db_session.scalars(select(AuditLog)).all()
    assert [row.target for row in remaining] == ["recent"]


def test_prune_old_audit_events_keeps_rows_within_the_window(db_session):
    _clear_audit_log(db_session)
    now = datetime.now(UTC)
    _insert_row(db_session, occurred_at=now - timedelta(days=_RETENTION_DAYS - 1), target="recent")

    deleted = prune_old_audit_events(db_session, retention_days=_RETENTION_DAYS)

    assert deleted == 0
    assert db_session.scalar(select(AuditLog)) is not None


def test_prune_old_audit_events_does_not_raise_against_an_empty_table(db_session):
    _clear_audit_log(db_session)

    deleted = prune_old_audit_events(db_session, retention_days=_RETENTION_DAYS)

    assert deleted == 0
    assert db_session.scalar(select(AuditLog)) is None
