from pathlib import Path

from app.models.cutting_job import CuttingJobOutputStatus, CuttingJobStatus

from cutting_worker.orchestrator import process_next_job
from tests.doubles import FakeS3Client, FakeVideoCutter, build_cutting_job

# Ticket #95's acceptance criteria, exercised end-to-end against a real
# (Alembic-migrated) database plus a fake S3 client and a fake VideoCutter —
# never real ffmpeg, real assist, or the real bucket (issue #93's testing
# strategy for this seam). Mirrors worker/tests/test_orchestrator.py's own
# shape.


def test_process_next_job_returns_false_when_queue_empty(db_session, work_root):
    processed = process_next_job(
        db_session, s3_client=FakeS3Client(), video_cutter=FakeVideoCutter(), work_root=work_root
    )

    assert processed is False


def test_process_next_job_succeeds_uploads_every_cut_and_discards_source(
    db_session, work_root, uploads_root
):
    job = build_cutting_job(db_session, cameras=("C1",), uploads_root=uploads_root)
    upload_path = job.c1_source_path
    s3_client = FakeS3Client()
    video_cutter = FakeVideoCutter()

    processed = process_next_job(
        db_session, s3_client=s3_client, video_cutter=video_cutter, work_root=work_root
    )

    assert processed is True
    db_session.refresh(job)
    assert job.status == CuttingJobStatus.SUCCEEDED
    assert job.started_at is not None
    assert job.finished_at is not None
    assert all(output.status == CuttingJobOutputStatus.SUCCEEDED for output in job.outputs)
    assert len(job.outputs) == 15  # ME_F1..ME_F8, ZE_F1..ZE_F7 — never ZE_F8

    for output in job.outputs:
        assert f"cuts/T001/T001_C1_{output.condition}_{output.phase}.mp4" in s3_client.objects

    assert not (work_root / str(job.id)).exists()
    # The source upload itself — its whole directory, not just the file —
    # is discarded on a fully-succeeded job.
    assert not Path(upload_path).parent.exists()


def test_process_next_job_missing_phase_output_fails_job_and_keeps_source(
    db_session, work_root, uploads_root
):
    job = build_cutting_job(db_session, cameras=("C1",), uploads_root=uploads_root)
    s3_client = FakeS3Client()
    video_cutter = FakeVideoCutter(missing_outputs=frozenset({("C1", "ME_F2")}))

    process_next_job(
        db_session, s3_client=s3_client, video_cutter=video_cutter, work_root=work_root
    )

    db_session.refresh(job)
    assert job.status == CuttingJobStatus.FAILED
    outputs_by_key = {
        (output.camera, f"{output.condition}_{output.phase}"): output for output in job.outputs
    }
    failed_output = outputs_by_key[("C1", "ME_F2")]
    assert failed_output.status == CuttingJobOutputStatus.FAILED
    assert failed_output.failure_reason
    succeeded_output = outputs_by_key[("C1", "ME_F1")]
    assert succeeded_output.status == CuttingJobOutputStatus.SUCCEEDED

    # A failed job keeps its source around for a later retry (ticket #95's
    # acceptance criteria) — its upload directory must still exist.
    upload_path = job.c1_source_path
    assert upload_path is not None
    assert Path(upload_path).is_file()


def test_process_next_job_both_cameras_computes_offset_and_uploads_all(
    db_session, work_root, uploads_root
):
    job = build_cutting_job(db_session, cameras=("C1", "C2"), uploads_root=uploads_root)
    s3_client = FakeS3Client()
    video_cutter = FakeVideoCutter()

    process_next_job(
        db_session, s3_client=s3_client, video_cutter=video_cutter, work_root=work_root
    )

    db_session.refresh(job)
    assert job.status == CuttingJobStatus.SUCCEEDED
    assert len(job.outputs) == 30  # 15 phases * 2 cameras
    assert len(video_cutter.calls) == 1
    assert set(video_cutter.calls[0]["source_paths"]) == {"C1", "C2"}


def test_process_next_job_whole_batch_failure_marks_every_output_failed(
    db_session, work_root, uploads_root
):
    job = build_cutting_job(db_session, cameras=("C1",), uploads_root=uploads_root)
    video_cutter = FakeVideoCutter(raises=RuntimeError("could not read source video"))

    process_next_job(
        db_session, s3_client=FakeS3Client(), video_cutter=video_cutter, work_root=work_root
    )

    db_session.refresh(job)
    assert job.status == CuttingJobStatus.FAILED
    assert all(output.status == CuttingJobOutputStatus.FAILED for output in job.outputs)
    for output in job.outputs:
        assert output.failure_reason
        assert "RuntimeError" not in output.failure_reason
        assert "could not read source video" not in output.failure_reason


class _RaisingUploadS3Client(FakeS3Client):
    """A FakeS3Client whose `upload_file` always raises — simulates a
    transient S3 error surfacing at the one point in `_run_claimed_job` that
    has no dedicated try/except of its own."""

    def upload_file(self, local_path, key) -> None:
        raise RuntimeError("simulated S3 upload failure")


def test_process_next_job_survives_an_unhandled_error_and_fails_only_that_job(
    db_session, work_root, uploads_root
):
    job = build_cutting_job(db_session, cameras=("C1",), uploads_root=uploads_root)
    s3_client = _RaisingUploadS3Client()
    video_cutter = FakeVideoCutter()

    processed = process_next_job(
        db_session, s3_client=s3_client, video_cutter=video_cutter, work_root=work_root
    )

    assert processed is True
    db_session.refresh(job)
    assert job.status == CuttingJobStatus.FAILED
    assert job.finished_at is not None
    assert all(output.status == CuttingJobOutputStatus.FAILED for output in job.outputs)
    assert not (work_root / str(job.id)).exists()


def test_process_next_job_processes_only_one_job_at_a_time(db_session, work_root, uploads_root):
    first_job = build_cutting_job(
        db_session, test_id="T001", cameras=("C1",), uploads_root=uploads_root
    )
    second_job = build_cutting_job(
        db_session, test_id="T002", cameras=("C1",), uploads_root=uploads_root
    )

    processed = process_next_job(
        db_session, s3_client=FakeS3Client(), video_cutter=FakeVideoCutter(), work_root=work_root
    )

    assert processed is True
    db_session.refresh(first_job)
    db_session.refresh(second_job)
    assert first_job.status == CuttingJobStatus.SUCCEEDED
    assert second_job.status == CuttingJobStatus.QUEUED
