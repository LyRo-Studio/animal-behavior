from pathlib import Path

from app.models.cutting_job import (
    CuttingJob,
    CuttingJobOutput,
    CuttingJobOutputStatus,
    CuttingJobStatus,
)
from sqlalchemy import func, select

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


# --- Live per-phase progress (ticket #98) ---


def _succeeded_output_count(db_session, job_id: int) -> int:
    """Counted in the database (not read off an in-memory object), the way
    `GET /cutting-jobs/{id}` would see it."""
    return db_session.scalar(
        select(func.count())
        .select_from(CuttingJobOutput)
        .where(
            CuttingJobOutput.cutting_job_id == job_id,
            CuttingJobOutput.status == CuttingJobOutputStatus.SUCCEEDED,
        )
    )


def test_outputs_succeed_one_by_one_while_the_job_is_still_running(
    db_session, work_root, uploads_root
):
    job = build_cutting_job(db_session, cameras=("C1",), uploads_root=uploads_root)
    s3_client = FakeS3Client()
    observed = []

    def observe(path: Path) -> None:
        job_status = db_session.scalar(select(CuttingJob.status).where(CuttingJob.id == job.id))
        observed.append(
            (
                _succeeded_output_count(db_session, job.id),
                job_status,
                f"cuts/T001/{path.name}" in s3_client.objects,
            )
        )

    process_next_job(
        db_session,
        s3_client=s3_client,
        video_cutter=FakeVideoCutter(after_each_output=observe),
        work_root=work_root,
    )

    # One more succeeded phase after each file, each already uploaded, all
    # before the job itself leaves `running`.
    assert observed == [(n, CuttingJobStatus.RUNNING, True) for n in range(1, 16)]
    db_session.refresh(job)
    assert job.status == CuttingJobStatus.SUCCEEDED


def test_a_phase_skipped_mid_run_leaves_only_its_own_output_pending_until_the_end(
    db_session, work_root, uploads_root
):
    """ME_F2 never gets a file: the phases after it still succeed live, and
    ME_F2 itself is only marked failed once the cutter has finished."""
    job = build_cutting_job(db_session, cameras=("C1",), uploads_root=uploads_root)
    observed = []

    process_next_job(
        db_session,
        s3_client=FakeS3Client(),
        video_cutter=FakeVideoCutter(
            missing_outputs=frozenset({("C1", "ME_F2")}),
            after_each_output=lambda path: observed.append(
                _succeeded_output_count(db_session, job.id)
            ),
        ),
        work_root=work_root,
    )

    assert observed == list(range(1, 15))
    db_session.refresh(job)
    assert job.status == CuttingJobStatus.FAILED
    statuses = {f"{o.condition}_{o.phase}": o.status for o in job.outputs}
    assert statuses["ME_F2"] == CuttingJobOutputStatus.FAILED
    assert statuses["ME_F3"] == CuttingJobOutputStatus.SUCCEEDED


def test_a_written_but_unreported_output_is_still_picked_up_after_the_cut(
    db_session, work_root, uploads_root
):
    """The post-run directory scan stays as a fallback: a file the cutter
    wrote without reporting it still counts."""
    job = build_cutting_job(db_session, cameras=("C1",), uploads_root=uploads_root)
    s3_client = FakeS3Client()

    process_next_job(
        db_session,
        s3_client=s3_client,
        video_cutter=FakeVideoCutter(report_outputs=False),
        work_root=work_root,
    )

    db_session.refresh(job)
    assert job.status == CuttingJobStatus.SUCCEEDED
    assert len([key for key in s3_client.objects if key.startswith("cuts/T001/")]) == 15


class _StrayOutputVideoCutter(FakeVideoCutter):
    """Reports a file no CuttingJobOutput expects before the real ones."""

    def cut(self, *, output_dir: Path, on_output, **kwargs) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        stray = output_dir / "T001_C1_notes.txt"
        stray.write_bytes(b"not a cut")
        on_output(stray)
        super().cut(output_dir=output_dir, on_output=on_output, **kwargs)


def test_a_reported_file_no_output_expects_is_ignored(db_session, work_root, uploads_root):
    job = build_cutting_job(db_session, cameras=("C1",), uploads_root=uploads_root)
    s3_client = FakeS3Client()

    process_next_job(
        db_session,
        s3_client=s3_client,
        video_cutter=_StrayOutputVideoCutter(),
        work_root=work_root,
    )

    db_session.refresh(job)
    assert job.status == CuttingJobStatus.SUCCEEDED
    assert "cuts/T001/T001_C1_notes.txt" not in s3_client.objects
