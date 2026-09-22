from datetime import time
from io import BytesIO

from openpyxl import Workbook

from app.models.cutting_job import CuttingJobOutputStatus, CuttingJobStatus
from app.services.cutting_jobs import (
    SourceVideoUpload,
    claim_next_queued_cutting_job,
    create_cutting_job,
    finalize_cutting_job,
    requeue_stuck_running_cutting_jobs,
)

# Ticket #95's own service-layer additions to app/services/cutting_jobs.py —
# the DB-transition primitives the cutting-worker (cutting-worker/cutting_worker
# /orchestrator.py) calls through, exercised here directly against a real
# (Alembic-migrated) database, mirroring test_analyses_worker_claim.py's own
# testing decisions. The cutting-worker's own end-to-end orchestration is
# covered in cutting-worker/tests instead.

_CONDITIONS = ("ME", "ZE")
_PHASE_HEADERS = [f"{condition}_F{n}" for condition in _CONDITIONS for n in range(1, 9)]
_HEADERS = ["Test ID", "Dog ID", "C1/C2", *_PHASE_HEADERS]


def _workbook_bytes(test_id="T001", *, reference_camera="C1") -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(_HEADERS)
    row = [test_id, "Rex", reference_camera]
    for index in range(len(_PHASE_HEADERS)):
        row.append(time(index + 1, 0, 0))
    sheet.append(row)
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _source_video(tmp_path, *, camera, test_id="T001"):
    path = tmp_path / f"{test_id}_{camera}_source.mp4"
    path.write_bytes(b"not a real video, just needs to exist")
    return SourceVideoUpload(camera=camera, local_path=path, filename=path.name)


def _create_job(db_session, s3_client, media_prober, tmp_path, *, test_id="T001"):
    return create_cutting_job(
        db_session,
        requested_by_identity="jan.peeters@vives.be",
        test_id=test_id,
        excel_bytes=_workbook_bytes(test_id=test_id),
        uploads=[_source_video(tmp_path, camera="C1", test_id=test_id)],
        s3=s3_client,
        media_prober=media_prober,
    )


def test_claim_next_queued_cutting_job_claims_oldest_and_sets_running(
    db_session, s3_client, media_prober, tmp_path
):
    older = _create_job(db_session, s3_client, media_prober, tmp_path, test_id="T001")
    _create_job(db_session, s3_client, media_prober, tmp_path, test_id="T002")

    claimed = claim_next_queued_cutting_job(db_session)

    assert claimed is not None
    assert claimed.id == older.id
    assert claimed.status == CuttingJobStatus.RUNNING
    assert claimed.started_at is not None


def test_claim_next_queued_cutting_job_returns_none_when_queue_empty(db_session):
    assert claim_next_queued_cutting_job(db_session) is None


def test_claim_next_queued_cutting_job_ignores_non_queued_jobs(
    db_session, s3_client, media_prober, tmp_path
):
    job = _create_job(db_session, s3_client, media_prober, tmp_path)
    job.status = CuttingJobStatus.RUNNING
    db_session.add(job)
    db_session.commit()

    assert claim_next_queued_cutting_job(db_session) is None


def test_finalize_cutting_job_all_outputs_succeeded_is_succeeded(
    db_session, s3_client, media_prober, tmp_path
):
    job = _create_job(db_session, s3_client, media_prober, tmp_path)
    for output in job.outputs:
        output.status = CuttingJobOutputStatus.SUCCEEDED
        db_session.add(output)
    db_session.commit()

    finalized = finalize_cutting_job(db_session, job)

    assert finalized.status == CuttingJobStatus.SUCCEEDED
    assert finalized.finished_at is not None


def test_finalize_cutting_job_no_outputs_is_failed_not_vacuously_succeeded(
    db_session, s3_client, media_prober, tmp_path
):
    # Regression test (caught in review): {...} <= {SUCCEEDED} is vacuously
    # true for an empty set, so a job with no CuttingJobOutput rows at all
    # must not be marked succeeded — that would discard its source upload
    # despite producing zero Cuts.
    job = _create_job(db_session, s3_client, media_prober, tmp_path)
    for output in list(job.outputs):
        db_session.delete(output)
    db_session.commit()
    db_session.refresh(job)
    assert job.outputs == []

    finalized = finalize_cutting_job(db_session, job)

    assert finalized.status == CuttingJobStatus.FAILED


def test_finalize_cutting_job_any_output_failed_is_failed(
    db_session, s3_client, media_prober, tmp_path
):
    job = _create_job(db_session, s3_client, media_prober, tmp_path)
    job.outputs[0].status = CuttingJobOutputStatus.SUCCEEDED
    job.outputs[1].status = CuttingJobOutputStatus.FAILED
    job.outputs[1].failure_reason = "This phase could not be cut."
    db_session.add(job)
    db_session.commit()

    finalized = finalize_cutting_job(db_session, job)

    assert finalized.status == CuttingJobStatus.FAILED


def test_requeue_stuck_running_cutting_jobs_resets_job_and_outputs(
    db_session, s3_client, media_prober, tmp_path
):
    job = _create_job(db_session, s3_client, media_prober, tmp_path)
    claimed = claim_next_queued_cutting_job(db_session)
    assert claimed.id == job.id
    claimed.outputs[0].status = CuttingJobOutputStatus.SUCCEEDED
    db_session.add(claimed)
    db_session.commit()

    requeued = requeue_stuck_running_cutting_jobs(db_session)

    assert [j.id for j in requeued] == [job.id]
    reset_job = requeued[0]
    assert reset_job.status == CuttingJobStatus.QUEUED
    assert reset_job.started_at is None
    assert all(output.status == CuttingJobOutputStatus.PENDING for output in reset_job.outputs)


def test_requeue_stuck_running_cutting_jobs_leaves_queued_and_terminal_jobs_alone(
    db_session, s3_client, media_prober, tmp_path
):
    _create_job(db_session, s3_client, media_prober, tmp_path)  # stays queued

    requeued = requeue_stuck_running_cutting_jobs(db_session)

    assert requeued == []
