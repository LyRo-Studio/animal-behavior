"""Direct tests of `record_audit_event` (ticket #82, issue #79), same shape
as `test_analyses.py`'s direct tests of `create_analysis_job`/
`finalize_analysis_job` — against a real (Alembic-migrated) test DB."""

from sqlalchemy import select

from app.models.audit_log import AuditAction, AuditLog
from app.services.audit_log import record_audit_event


def test_record_audit_event_writes_the_expected_row(db_session):
    record_audit_event(
        db_session,
        identity="jan.peeters@vives.be",
        identity_verified=True,
        action=AuditAction.ANALYSIS_STARTED,
        target_type="analysis_job",
        target="42",
    )
    db_session.commit()

    row = db_session.scalar(select(AuditLog))
    assert row is not None
    assert row.identity == "jan.peeters@vives.be"
    assert row.identity_verified is True
    assert row.action == AuditAction.ANALYSIS_STARTED
    assert row.target_type == "analysis_job"
    assert row.target == "42"
    assert row.failure_reason is None
    assert row.occurred_at is not None


def test_record_audit_event_records_a_failure_reason(db_session):
    record_audit_event(
        db_session,
        identity=None,
        identity_verified=False,
        action=AuditAction.ANALYSIS_FAILED,
        target_type="analysis_job",
        target="7",
        failure_reason="No video succeeded.",
    )
    db_session.commit()

    row = db_session.scalar(select(AuditLog))
    assert row.identity is None
    assert row.failure_reason == "No video succeeded."


def test_record_audit_event_swallows_a_write_failure_without_raising(db_session):
    """`target_type` is a `String(50)` column — a value over that length
    forces a real DB-level constraint error (Postgres raises "value too
    long for type character varying(50)") to exercise the SAVEPOINT-
    isolated failure path, rather than passing a type the signature
    already rules out statically."""
    record_audit_event(
        db_session,
        identity="jan.peeters@vives.be",
        identity_verified=True,
        action=AuditAction.ANALYSIS_STARTED,
        target_type="x" * 51,
        target="42",
    )

    assert db_session.scalar(select(AuditLog)) is None


def test_record_audit_event_failure_does_not_affect_a_subsequent_unrelated_write(db_session):
    """Proves the SAVEPOINT isolation: a forced failure must not poison the
    caller's own session for whatever it does next."""
    record_audit_event(
        db_session,
        identity="jan.peeters@vives.be",
        identity_verified=True,
        action=AuditAction.ANALYSIS_STARTED,
        target_type="x" * 51,
        target="42",
    )

    record_audit_event(
        db_session,
        identity="jan.peeters@vives.be",
        identity_verified=True,
        action=AuditAction.ANALYSIS_STARTED,
        target_type="analysis_job",
        target="42",
    )
    db_session.commit()

    row = db_session.scalar(select(AuditLog))
    assert row is not None
    assert row.target_type == "analysis_job"
