"""Direct tests for app/services/cutting_jobs.py (ticket #94, part of issue
#93's Feature C) — exercised entirely through `create_cutting_job`/
`get_cutting_job` against the real (Alembic-migrated) database, mirroring
`test_analyses.py`'s own testing decisions (issue #93's Test seams). No
cutting-worker exists yet: a created job just sits `queued`.
"""

from datetime import time
from io import BytesIO

import pytest
from openpyxl import Workbook

from app.models.cutting_job import CuttingJob, CuttingJobOutputStatus, CuttingJobStatus
from app.services.cutting_jobs import (
    CuttingJobNotFoundError,
    DuplicateCameraUploadError,
    InvalidSourceFilenameError,
    NoSourceVideoUploadedError,
    ReferenceCameraMismatchError,
    SourceVideoCollisionError,
    SourceVideoNotDecodableError,
    SourceVideoUpload,
    create_cutting_job,
    get_cutting_job,
)
from app.services.media_prober import MediaProbeError
from app.services.timestamp_excel import TestRowNotFoundError

_CONDITIONS = ("ME", "ZE")
_PHASE_HEADERS = [f"{condition}_F{n}" for condition in _CONDITIONS for n in range(1, 9)]
_HEADERS = ["Test ID", "Dog ID", "C1/C2", *_PHASE_HEADERS]


def _workbook_bytes(
    test_id="T001", *, reference_camera="C1", dog_id="Rex", phases=_PHASE_HEADERS
) -> bytes:
    """A workbook with one row for `test_id`, every header in `phases` given
    a distinct, valid (non-skipped) elapsed time, everything else blank
    (skipped)."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(_HEADERS)
    row = [test_id, dog_id, reference_camera]
    for index, header in enumerate(_PHASE_HEADERS):
        row.append(time(index + 1, 0, 0) if header in phases else None)
    sheet.append(row)
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _source_video(tmp_path, *, camera, test_id="T001", suffix="source"):
    path = tmp_path / f"{test_id}_{camera}_{suffix}.mp4"
    path.write_bytes(b"not a real video, just needs to exist")
    return SourceVideoUpload(camera=camera, local_path=path, filename=path.name)


def test_create_cutting_job_with_a_single_matching_reference_camera(
    db_session, s3_client, media_prober, tmp_path
):
    excel = _workbook_bytes(reference_camera="C1")
    upload = _source_video(tmp_path, camera="C1")

    job = create_cutting_job(
        db_session,
        requested_by_identity="jan.peeters@vives.be",
        test_id="T001",
        excel_bytes=excel,
        uploads=[upload],
        s3=s3_client,
        media_prober=media_prober,
    )

    assert job.id is not None
    assert job.test_id == "T001"
    assert job.status == CuttingJobStatus.QUEUED
    assert job.reference_camera == "C1"
    assert job.requested_by_identity == "jan.peeters@vives.be"
    assert job.c1_source_path == str(upload.local_path)
    assert job.c2_source_path is None
    # 8 ME + 7 ZE (ZE_F8 structurally excluded) = 15 expected outputs.
    assert len(job.outputs) == 15
    assert all(output.camera == "C1" for output in job.outputs)
    assert all(output.status == CuttingJobOutputStatus.PENDING for output in job.outputs)
    assert ("ZE", "F8") not in {(o.condition, o.phase) for o in job.outputs}


def test_create_cutting_job_normalizes_a_bare_numeric_test_id_before_matching_filenames(
    db_session, s3_client, media_prober, tmp_path
):
    """Regression: filename/camera validation must use the same normalized
    Test ID the Excel row itself normalizes to, or a legitimately-named
    "T513_C1_..." upload is falsely rejected against a still-bare-numeric
    "513" request test_id (found in code review)."""
    excel = _workbook_bytes(test_id="513", reference_camera="C1")
    upload = _source_video(tmp_path, camera="C1", test_id="T513")

    job = create_cutting_job(
        db_session,
        requested_by_identity=None,
        test_id="513",
        excel_bytes=excel,
        uploads=[upload],
        s3=s3_client,
        media_prober=media_prober,
    )

    assert job.test_id == "T513"


def test_create_cutting_job_with_both_cameras_doubles_expected_outputs(
    db_session, s3_client, media_prober, tmp_path
):
    excel = _workbook_bytes(reference_camera="C1")
    uploads = [_source_video(tmp_path, camera="C1"), _source_video(tmp_path, camera="C2")]

    job = create_cutting_job(
        db_session,
        requested_by_identity=None,
        test_id="T001",
        excel_bytes=excel,
        uploads=uploads,
        s3=s3_client,
        media_prober=media_prober,
    )

    assert job.c1_source_path is not None
    assert job.c2_source_path is not None
    assert len(job.outputs) == 30
    assert {output.camera for output in job.outputs} == {"C1", "C2"}


def test_create_cutting_job_only_produces_outputs_for_non_skipped_phases(
    db_session, s3_client, media_prober, tmp_path
):
    excel = _workbook_bytes(reference_camera="C1", phases=["ME_F1", "ME_F2"])
    upload = _source_video(tmp_path, camera="C1")

    job = create_cutting_job(
        db_session,
        requested_by_identity=None,
        test_id="T001",
        excel_bytes=excel,
        uploads=[upload],
        s3=s3_client,
        media_prober=media_prober,
    )

    assert {(o.condition, o.phase) for o in job.outputs} == {("ME", "F1"), ("ME", "F2")}


def test_create_cutting_job_rejects_an_empty_upload_list(db_session, s3_client, media_prober):
    excel = _workbook_bytes()

    with pytest.raises(NoSourceVideoUploadedError):
        create_cutting_job(
            db_session,
            requested_by_identity=None,
            test_id="T001",
            excel_bytes=excel,
            uploads=[],
            s3=s3_client,
            media_prober=media_prober,
        )
    assert db_session.query(CuttingJob).count() == 0


def test_create_cutting_job_rejects_a_duplicate_camera_upload(
    db_session, s3_client, media_prober, tmp_path
):
    excel = _workbook_bytes()
    uploads = [
        _source_video(tmp_path, camera="C1"),
        _source_video(tmp_path, camera="C1", suffix="dup"),
    ]

    with pytest.raises(DuplicateCameraUploadError):
        create_cutting_job(
            db_session,
            requested_by_identity=None,
            test_id="T001",
            excel_bytes=excel,
            uploads=uploads,
            s3=s3_client,
            media_prober=media_prober,
        )
    assert db_session.query(CuttingJob).count() == 0


def test_create_cutting_job_rejects_a_filename_not_matching_its_camera(
    db_session, s3_client, media_prober, tmp_path
):
    excel = _workbook_bytes(reference_camera="C1")
    upload = _source_video(tmp_path, camera="C1")
    mismatched = SourceVideoUpload(
        camera="C1", local_path=upload.local_path, filename="T001_C2_source.mp4"
    )

    with pytest.raises(InvalidSourceFilenameError):
        create_cutting_job(
            db_session,
            requested_by_identity=None,
            test_id="T001",
            excel_bytes=excel,
            uploads=[mismatched],
            s3=s3_client,
            media_prober=media_prober,
        )
    assert db_session.query(CuttingJob).count() == 0


def test_create_cutting_job_rejects_a_single_non_reference_camera(
    db_session, s3_client, media_prober, tmp_path
):
    excel = _workbook_bytes(reference_camera="C2")
    upload = _source_video(tmp_path, camera="C1")

    with pytest.raises(ReferenceCameraMismatchError) as exc_info:
        create_cutting_job(
            db_session,
            requested_by_identity=None,
            test_id="T001",
            excel_bytes=excel,
            uploads=[upload],
            s3=s3_client,
            media_prober=media_prober,
        )
    assert exc_info.value.uploaded_camera == "C1"
    assert exc_info.value.reference_camera == "C2"
    assert db_session.query(CuttingJob).count() == 0


def test_create_cutting_job_accepts_the_non_reference_camera_when_both_are_uploaded(
    db_session, s3_client, media_prober, tmp_path
):
    """The reference-camera check only applies to a single-camera upload —
    with both present, either can be the "extra" one."""
    excel = _workbook_bytes(reference_camera="C2")
    uploads = [_source_video(tmp_path, camera="C1"), _source_video(tmp_path, camera="C2")]

    job = create_cutting_job(
        db_session,
        requested_by_identity=None,
        test_id="T001",
        excel_bytes=excel,
        uploads=uploads,
        s3=s3_client,
        media_prober=media_prober,
    )

    assert job.reference_camera == "C2"


def test_create_cutting_job_rejects_when_no_excel_row_matches_the_test(
    db_session, s3_client, media_prober, tmp_path
):
    excel = _workbook_bytes(test_id="T002")
    upload = _source_video(tmp_path, camera="C1")

    with pytest.raises(TestRowNotFoundError):
        create_cutting_job(
            db_session,
            requested_by_identity=None,
            test_id="T001",
            excel_bytes=excel,
            uploads=[upload],
            s3=s3_client,
            media_prober=media_prober,
        )
    assert db_session.query(CuttingJob).count() == 0


def test_create_cutting_job_rejects_an_undecodable_video(
    db_session, s3_client, media_prober, tmp_path
):
    media_prober.error = MediaProbeError("ffprobe failed")
    excel = _workbook_bytes(reference_camera="C1")
    upload = _source_video(tmp_path, camera="C1")

    with pytest.raises(SourceVideoNotDecodableError) as exc_info:
        create_cutting_job(
            db_session,
            requested_by_identity=None,
            test_id="T001",
            excel_bytes=excel,
            uploads=[upload],
            s3=s3_client,
            media_prober=media_prober,
        )
    assert exc_info.value.camera == "C1"
    assert db_session.query(CuttingJob).count() == 0


def test_create_cutting_job_rejects_a_filename_colliding_with_an_existing_s3_object(
    db_session, s3_client, media_prober, tmp_path
):
    excel = _workbook_bytes(reference_camera="C1")
    upload = _source_video(tmp_path, camera="C1")
    s3_client.objects[f"source/T001/{upload.filename}"] = b"already there"

    with pytest.raises(SourceVideoCollisionError):
        create_cutting_job(
            db_session,
            requested_by_identity=None,
            test_id="T001",
            excel_bytes=excel,
            uploads=[upload],
            s3=s3_client,
            media_prober=media_prober,
        )
    assert db_session.query(CuttingJob).count() == 0


def test_get_cutting_job_returns_the_created_job(db_session, s3_client, media_prober, tmp_path):
    excel = _workbook_bytes(reference_camera="C1")
    upload = _source_video(tmp_path, camera="C1")
    created = create_cutting_job(
        db_session,
        requested_by_identity=None,
        test_id="T001",
        excel_bytes=excel,
        uploads=[upload],
        s3=s3_client,
        media_prober=media_prober,
    )

    fetched = get_cutting_job(db_session, cutting_job_id=created.id)

    assert fetched.id == created.id


def test_get_cutting_job_for_unknown_id_raises(db_session):
    with pytest.raises(CuttingJobNotFoundError):
        get_cutting_job(db_session, cutting_job_id=999999)
