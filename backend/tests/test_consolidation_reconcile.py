"""Service-seam tests for reconciling Consolidations stuck in `processing`
(ticket #121). Real migrated Postgres, fake S3 — `reconcile_stale_
consolidations` is called directly, never through the lifespan task that
runs it in production (tests/test_main_lifespan.py covers that wiring).
"""

import logging
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import app.services.consolidation as consolidation_service
from alembic import command
from alembic.config import Config
from app.models.audit_log import AuditAction, AuditLog
from app.models.consolidation import Consolidation, ConsolidationCondition, ConsolidationStatus
from app.services.consolidation import reconcile_stale_consolidations, start_consolidation
from tests.conftest import _ALEMBIC_INI
from tests.fakes import FakeS3Client

_STALE_AFTER = timedelta(minutes=30)
_STALE_REASON = "Consolidation was interrupted: still processing after 30 minutes."


def _aged_consolidation(
    db: Session,
    *,
    age: timedelta,
    status: ConsolidationStatus = ConsolidationStatus.PROCESSING,
) -> Consolidation:
    """A Consolidation created `age` ago, left in `status`."""
    consolidation = start_consolidation(
        db,
        requested_by_identity=None,
        original_filename="export.xlsx",
        condition=ConsolidationCondition.ME,
        input_size_bytes=1,
    )
    consolidation.created_at = datetime.now(UTC) - age
    consolidation.status = status
    db.commit()
    db.refresh(consolidation)
    return consolidation


def _failed_audit_rows(db: Session, consolidation_id: int) -> list[AuditLog]:
    return list(
        db.scalars(
            select(AuditLog).where(
                AuditLog.action == AuditAction.CONSOLIDATION_FAILED,
                AuditLog.target == str(consolidation_id),
            )
        )
    )


def test_a_new_consolidation_records_its_result_key_at_creation(db_session):
    """So a result uploaded by a run that never finished can still be found
    and cleaned up (ticket #121)."""
    consolidation = start_consolidation(
        db_session,
        requested_by_identity=None,
        original_filename="export.xlsx",
        condition=ConsolidationCondition.ME,
        input_size_bytes=1,
    )

    key = consolidation.result_storage_key
    assert key is not None
    assert key.startswith("consolidations/")
    assert key.endswith("/result.xlsx")
    other = start_consolidation(
        db_session,
        requested_by_identity=None,
        original_filename="export.xlsx",
        condition=ConsolidationCondition.ME,
        input_size_bytes=1,
    )
    assert other.result_storage_key != key


def test_reconcile_marks_a_stale_processing_consolidation_failed(db_session):
    stale = _aged_consolidation(db_session, age=timedelta(minutes=31))

    reconciled = reconcile_stale_consolidations(
        db_session, s3=FakeS3Client(), stale_after=_STALE_AFTER
    )

    assert reconciled == [stale.id]
    db_session.expire_all()
    row = db_session.get(Consolidation, stale.id)
    assert row.status == ConsolidationStatus.FAILED
    assert row.failure_reason == _STALE_REASON
    assert row.completed_at is not None


def test_reconcile_writes_a_failed_audit_row_for_a_stale_consolidation(db_session):
    """Pairs the orphaned CONSOLIDATION_STARTED. A system action: there's no
    request, so no identity."""
    stale = _aged_consolidation(db_session, age=timedelta(hours=2))

    reconcile_stale_consolidations(db_session, s3=FakeS3Client(), stale_after=_STALE_AFTER)

    [row] = _failed_audit_rows(db_session, stale.id)
    assert row.target_type == "consolidation"
    assert row.failure_reason == _STALE_REASON
    assert row.identity is None
    assert row.identity_verified is False


def test_reconcile_leaves_a_processing_consolidation_younger_than_the_threshold(db_session):
    fresh = _aged_consolidation(db_session, age=timedelta(minutes=29))

    reconciled = reconcile_stale_consolidations(
        db_session, s3=FakeS3Client(), stale_after=_STALE_AFTER
    )

    assert reconciled == []
    db_session.expire_all()
    assert db_session.get(Consolidation, fresh.id).status == ConsolidationStatus.PROCESSING
    assert _failed_audit_rows(db_session, fresh.id) == []


@pytest.mark.parametrize("status", [ConsolidationStatus.COMPLETED, ConsolidationStatus.FAILED])
def test_reconcile_leaves_finished_consolidations_untouched(db_session, status):
    finished = _aged_consolidation(db_session, age=timedelta(days=1), status=status)
    s3 = FakeS3Client(objects={finished.result_storage_key: b"result"})

    reconciled = reconcile_stale_consolidations(db_session, s3=s3, stale_after=_STALE_AFTER)

    assert reconciled == []
    db_session.expire_all()
    row = db_session.get(Consolidation, finished.id)
    assert row.status == status
    assert row.failure_reason is None
    assert _failed_audit_rows(db_session, finished.id) == []
    assert finished.result_storage_key in s3.objects


def test_reconciling_twice_reconciles_and_audits_a_consolidation_only_once(db_session):
    stale = _aged_consolidation(db_session, age=timedelta(hours=1))
    s3 = FakeS3Client()

    first = reconcile_stale_consolidations(db_session, s3=s3, stale_after=_STALE_AFTER)
    second = reconcile_stale_consolidations(db_session, s3=s3, stale_after=_STALE_AFTER)

    assert first == [stale.id]
    assert second == []
    assert len(_failed_audit_rows(db_session, stale.id)) == 1


def test_reconcile_removes_an_orphaned_result_object(db_session):
    """The run may have uploaded its result before dying (e.g. the database
    failed at its final commit)."""
    stale = _aged_consolidation(db_session, age=timedelta(hours=1))
    kept = _aged_consolidation(db_session, age=timedelta(minutes=1))
    s3 = FakeS3Client(
        objects={stale.result_storage_key: b"orphan", kept.result_storage_key: b"in progress"}
    )

    reconcile_stale_consolidations(db_session, s3=s3, stale_after=_STALE_AFTER)

    assert s3.objects == {kept.result_storage_key: b"in progress"}


def test_reconcile_keeps_the_failed_status_when_cleaning_up_storage_fails(
    db_session, caplog, monkeypatch
):
    """Cleanup is best-effort: logged, and it never undoes the reconciliation
    or stops the other rows from being cleaned up."""
    monkeypatch.setattr(
        logging.getLogger("app.services.consolidation"), "disabled", False
    )  # alembic's fileConfig disables existing loggers; see test_consolidations_api.py
    first = _aged_consolidation(db_session, age=timedelta(hours=2))
    second = _aged_consolidation(db_session, age=timedelta(hours=1))
    s3 = FakeS3Client(
        objects={first.result_storage_key: b"orphan", second.result_storage_key: b"orphan"}
    )
    real_delete_object = s3.delete_object

    def _fail_for_first(key):
        if key == first.result_storage_key:
            raise RuntimeError("simulated storage outage")
        real_delete_object(key)

    s3.delete_object = _fail_for_first

    reconciled = reconcile_stale_consolidations(db_session, s3=s3, stale_after=_STALE_AFTER)

    assert sorted(reconciled) == sorted([first.id, second.id])
    db_session.expire_all()
    assert db_session.get(Consolidation, first.id).status == ConsolidationStatus.FAILED
    assert first.result_storage_key in s3.objects
    assert second.result_storage_key not in s3.objects
    assert "simulated storage outage" in caplog.text


def test_reconcile_skips_cleanup_for_a_consolidation_without_a_result_key(db_session):
    """Rows created before ticket #121 never recorded a key up front."""
    stale = _aged_consolidation(db_session, age=timedelta(hours=1))
    stale.result_storage_key = None
    db_session.commit()
    s3 = FakeS3Client(objects={"consolidations/unrelated/result.xlsx": b"x"})

    reconciled = reconcile_stale_consolidations(db_session, s3=s3, stale_after=_STALE_AFTER)

    assert reconciled == [stale.id]
    assert s3.objects == {"consolidations/unrelated/result.xlsx": b"x"}


def test_reconcile_still_fails_stale_rows_when_storage_is_unavailable(
    db_session, caplog, monkeypatch
):
    """Without a storage client (e.g. S3 misconfigured), the database side
    still happens — a storage problem must not keep stuck rows stuck — and
    the skipped cleanup is logged."""
    monkeypatch.setattr(logging.getLogger("app.services.consolidation"), "disabled", False)
    stale = _aged_consolidation(db_session, age=timedelta(hours=1))

    reconciled = reconcile_stale_consolidations(db_session, s3=None, stale_after=_STALE_AFTER)

    assert reconciled == [stale.id]
    db_session.expire_all()
    assert db_session.get(Consolidation, stale.id).status == ConsolidationStatus.FAILED
    assert stale.result_storage_key in caplog.text


def test_reconcile_changes_nothing_if_the_audit_row_cannot_be_written(
    scratch_database, monkeypatch
):
    """The status change and its CONSOLIDATION_FAILED row commit together,
    or not at all. Needs real commits and rollbacks, which db_session's
    per-test SAVEPOINT harness can't show, so this runs against a scratch
    database."""
    command.upgrade(Config(str(_ALEMBIC_INI)), "head")
    engine = create_engine(scratch_database)
    try:
        with Session(engine) as db:
            stale_id = _aged_consolidation(db, age=timedelta(hours=1)).id

        def _raise(*args, **kwargs):
            raise RuntimeError("simulated audit write failure")

        monkeypatch.setattr(consolidation_service, "record_required_audit_event", _raise)
        with Session(engine) as db, pytest.raises(RuntimeError):
            reconcile_stale_consolidations(db, s3=FakeS3Client(), stale_after=_STALE_AFTER)

        with Session(engine) as db:
            assert db.get(Consolidation, stale_id).status == ConsolidationStatus.PROCESSING
            assert _failed_audit_rows(db, stale_id) == []
    finally:
        engine.dispose()
