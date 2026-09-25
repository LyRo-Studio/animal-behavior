"""Service-seam tests for removing stored consolidation results that no
Consolidation references (ticket #149): migration 0016 deletes every
old-format Consolidation, but a migration can't reach object storage, so
their results are removed by this separate, re-runnable step. Real migrated
Postgres, fake S3 — same seam as test_consolidation_reconcile.py.
"""

from app.services.consolidation import (
    remove_unreferenced_consolidation_results,
    start_consolidation,
)
from tests.fakes import FakeS3Client

_ORPHANED_KEY = "consolidations/0123456789abcdef/result.xlsx"


def _consolidation_key(db_session) -> str:
    consolidation = start_consolidation(
        db_session,
        requested_by_identity=None,
        original_filename="export.xlsx",
        input_size_bytes=1,
    )
    assert consolidation.result_storage_key is not None
    return consolidation.result_storage_key


def test_removes_a_result_no_consolidation_references(db_session):
    s3 = FakeS3Client(objects={_ORPHANED_KEY: b"old-format result"})

    removed = remove_unreferenced_consolidation_results(db_session, s3=s3)

    assert removed == [_ORPHANED_KEY]
    assert s3.objects == {}


def test_keeps_every_referenced_result_and_every_other_object(db_session):
    referenced_key = _consolidation_key(db_session)
    others = {
        referenced_key: b"current result",
        "reports/42/report.pdf": b"analysis report",
        "T001/C1/ME_F1.mp4": b"cut",
        "consolidations.xlsx": b"not under the consolidation prefix",
    }
    s3 = FakeS3Client(objects={**others, _ORPHANED_KEY: b"old-format result"})

    remove_unreferenced_consolidation_results(db_session, s3=s3)

    assert s3.objects == others


def test_running_it_again_removes_nothing_more(db_session):
    referenced_key = _consolidation_key(db_session)
    s3 = FakeS3Client(objects={referenced_key: b"current", _ORPHANED_KEY: b"old"})
    remove_unreferenced_consolidation_results(db_session, s3=s3)

    removed = remove_unreferenced_consolidation_results(db_session, s3=s3)

    assert removed == []
    assert s3.objects == {referenced_key: b"current"}
