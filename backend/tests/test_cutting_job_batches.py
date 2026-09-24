"""Direct tests for app/services/cutting_jobs.py's batch submission (ticket
#97, part of issue #93's Feature C): up to 5 Tests per submission, each its
own independent CuttingJob. Runs against the real (Alembic-migrated) test
database and real upload records on disk (app/services/cutting_uploads.py),
since a batch entry names its source videos by upload id.
"""

import asyncio
from datetime import time
from io import BytesIO

import pytest
from openpyxl import Workbook

from app.models.cutting_job import CuttingJob, CuttingJobStatus
from app.services.cutting_jobs import (
    MAX_TESTS_PER_BATCH,
    CutsAlreadyExistError,
    CuttingJobSubmission,
    DuplicateTestInBatchError,
    EmptyBatchError,
    ReferenceCameraMismatchError,
    TooManyTestsInBatchError,
    UnknownSourceUploadError,
    get_cutting_job,
    submit_cutting_job_batch,
)
from app.services.cutting_uploads import append_upload_chunk, start_upload
from app.services.timestamp_excel import MalformedTimestampCellError

_PHASE_HEADERS = [f"{condition}_F{n}" for condition in ("ME", "ZE") for n in range(1, 9)]
_HEADERS = ["Test ID", "Dog ID", "C1/C2", *_PHASE_HEADERS]


def _workbook_bytes(*test_ids: str, reference_camera="C1") -> bytes:
    """One row per Test in `test_ids`, each with ME_F1/ME_F2 set and every
    other phase blank (skipped)."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(_HEADERS)
    for test_id in test_ids:
        phases = [time(n, 0, 0) if n <= 2 else None for n in range(1, len(_PHASE_HEADERS) + 1)]
        sheet.append([test_id, "Rex", reference_camera, *phases])
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


async def _chunks(*parts: bytes):
    for part in parts:
        yield part


def _completed_upload(root, *, test_id, camera="C1") -> str:
    status = start_upload(
        root,
        test_id=test_id,
        camera=camera,
        filename=f"{test_id}_{camera}_source.mp4",
        total_size_bytes=4,
    )
    asyncio.run(
        append_upload_chunk(
            root, status.upload_id, expected_offset=0, chunk_stream=_chunks(b"data")
        )
    )
    return status.upload_id


def _submit(db_session, s3_client, media_prober, root, submissions, *, test_ids=None):
    return submit_cutting_job_batch(
        db_session,
        requested_by_identity="jan.peeters@vives.be",
        excel_bytes=_workbook_bytes(*(test_ids or [s.test_id for s in submissions])),
        submissions=submissions,
        upload_root=root,
        s3=s3_client,
        media_prober=media_prober,
    )


def test_a_batch_creates_one_independent_job_per_test_in_submission_order(
    db_session, s3_client, media_prober, tmp_path
):
    submissions = [
        CuttingJobSubmission(
            test_id=test_id, c1_upload_id=_completed_upload(tmp_path, test_id=test_id)
        )
        for test_id in ("T003", "T001", "T002")
    ]

    results = _submit(db_session, s3_client, media_prober, tmp_path, submissions)

    assert [result.test_id for result in results] == ["T003", "T001", "T002"]
    assert all(result.error is None for result in results)
    jobs = [result.job for result in results]
    assert [job.test_id for job in jobs] == ["T003", "T001", "T002"]
    assert len({job.id for job in jobs}) == 3
    for job in jobs:
        assert job.status == CuttingJobStatus.QUEUED
        assert job.requested_by_identity == "jan.peeters@vives.be"
        assert get_cutting_job(db_session, cutting_job_id=job.id).test_id == job.test_id


def test_a_batch_of_exactly_the_maximum_is_accepted(db_session, s3_client, media_prober, tmp_path):
    test_ids = [f"T00{n}" for n in range(1, MAX_TESTS_PER_BATCH + 1)]
    submissions = [
        CuttingJobSubmission(
            test_id=test_id, c1_upload_id=_completed_upload(tmp_path, test_id=test_id)
        )
        for test_id in test_ids
    ]

    results = _submit(db_session, s3_client, media_prober, tmp_path, submissions)

    assert all(result.job is not None for result in results)
    assert db_session.query(CuttingJob).count() == MAX_TESTS_PER_BATCH


def test_a_batch_over_the_maximum_is_rejected_outright_with_no_jobs_created(
    db_session, s3_client, media_prober, tmp_path
):
    """Every entry here is individually valid — the size alone rejects it."""
    test_ids = [f"T00{n}" for n in range(1, MAX_TESTS_PER_BATCH + 2)]
    submissions = [
        CuttingJobSubmission(
            test_id=test_id, c1_upload_id=_completed_upload(tmp_path, test_id=test_id)
        )
        for test_id in test_ids
    ]

    with pytest.raises(TooManyTestsInBatchError) as exc_info:
        _submit(db_session, s3_client, media_prober, tmp_path, submissions)

    assert exc_info.value.count == MAX_TESTS_PER_BATCH + 1
    assert db_session.query(CuttingJob).count() == 0


def test_an_empty_batch_is_rejected(db_session, s3_client, media_prober, tmp_path):
    with pytest.raises(EmptyBatchError):
        _submit(db_session, s3_client, media_prober, tmp_path, [], test_ids=["T001"])


def test_the_same_test_twice_in_one_batch_is_rejected_outright(
    db_session, s3_client, media_prober, tmp_path
):
    """Two jobs for one Test would race to write the same cuts/<Test>/ keys.
    "513" and "T513" are the same Test once normalized."""
    submissions = [
        CuttingJobSubmission(
            test_id="T513", c1_upload_id=_completed_upload(tmp_path, test_id="T513")
        ),
        CuttingJobSubmission(
            test_id="T001", c1_upload_id=_completed_upload(tmp_path, test_id="T001")
        ),
        CuttingJobSubmission(
            test_id="513", c1_upload_id=_completed_upload(tmp_path, test_id="T513")
        ),
    ]

    with pytest.raises(DuplicateTestInBatchError) as exc_info:
        _submit(
            db_session, s3_client, media_prober, tmp_path, submissions, test_ids=["T513", "T001"]
        )

    assert exc_info.value.test_id == "T513"
    assert db_session.query(CuttingJob).count() == 0


def test_one_tests_validation_failure_does_not_block_the_others(
    db_session, s3_client, media_prober, tmp_path
):
    """T002 only uploaded C2, but the workbook's reference camera is C1."""
    submissions = [
        CuttingJobSubmission(
            test_id="T001", c1_upload_id=_completed_upload(tmp_path, test_id="T001")
        ),
        CuttingJobSubmission(
            test_id="T002", c2_upload_id=_completed_upload(tmp_path, test_id="T002", camera="C2")
        ),
        CuttingJobSubmission(
            test_id="T003", c1_upload_id=_completed_upload(tmp_path, test_id="T003")
        ),
    ]

    results = _submit(db_session, s3_client, media_prober, tmp_path, submissions)

    by_test = {result.test_id: result for result in results}
    assert by_test["T001"].job is not None
    assert by_test["T003"].job is not None
    assert by_test["T002"].job is None
    assert isinstance(by_test["T002"].error, ReferenceCameraMismatchError)
    assert {job.test_id for job in db_session.query(CuttingJob)} == {"T001", "T003"}


def test_a_malformed_cell_in_one_tests_row_fails_only_that_test(
    db_session, s3_client, media_prober, tmp_path
):
    """The batch shares one workbook; T002's own bad cell must not take
    T001 down with it (issue #93 user story 10)."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(_HEADERS)
    blank_phases = [None] * (len(_PHASE_HEADERS) - 1)
    sheet.append(["T001", "Rex", "C1", time(1, 0, 0), *blank_phases])
    sheet.append(["T002", "Rex", "C1", "-", *blank_phases])
    buffer = BytesIO()
    workbook.save(buffer)

    results = submit_cutting_job_batch(
        db_session,
        requested_by_identity=None,
        excel_bytes=buffer.getvalue(),
        submissions=[
            CuttingJobSubmission(
                test_id=test_id, c1_upload_id=_completed_upload(tmp_path, test_id=test_id)
            )
            for test_id in ("T001", "T002")
        ],
        upload_root=tmp_path,
        s3=s3_client,
        media_prober=media_prober,
    )

    assert results[0].job is not None
    assert isinstance(results[1].error, MalformedTimestampCellError)


def test_an_unknown_upload_id_fails_only_its_own_test(
    db_session, s3_client, media_prober, tmp_path
):
    submissions = [
        CuttingJobSubmission(test_id="T001", c1_upload_id="0" * 32),
        CuttingJobSubmission(
            test_id="T002", c1_upload_id=_completed_upload(tmp_path, test_id="T002")
        ),
    ]

    results = _submit(db_session, s3_client, media_prober, tmp_path, submissions)

    assert isinstance(results[0].error, UnknownSourceUploadError)
    assert results[0].error.camera == "C1"
    assert results[1].job is not None


def test_recut_confirmation_is_per_test(db_session, s3_client, media_prober, tmp_path):
    """Ticket #96's gate applies to each Test on its own: T001 already has
    Cuts and isn't confirmed; T002 already has Cuts and is; T003 has none."""
    s3_client.objects["cuts/T001/T001_C1_ME_F1.mp4"] = b"an earlier cut"
    s3_client.objects["cuts/T002/T002_C1_ME_F1.mp4"] = b"an earlier cut"
    submissions = [
        CuttingJobSubmission(
            test_id="T001", c1_upload_id=_completed_upload(tmp_path, test_id="T001")
        ),
        CuttingJobSubmission(
            test_id="T002",
            c1_upload_id=_completed_upload(tmp_path, test_id="T002"),
            confirm_overwrite=True,
        ),
        CuttingJobSubmission(
            test_id="T003", c1_upload_id=_completed_upload(tmp_path, test_id="T003")
        ),
    ]

    results = _submit(db_session, s3_client, media_prober, tmp_path, submissions)

    assert isinstance(results[0].error, CutsAlreadyExistError)
    assert results[1].job is not None
    assert results[2].job is not None
