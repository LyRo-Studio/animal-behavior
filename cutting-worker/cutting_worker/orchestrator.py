"""Claims and fully processes one CuttingJob (ticket #95, part of issue
#93's Feature C): runs its uploaded source video(s) through `VideoCutter`,
uploads every produced Cut to `cuts/<Test>/`, discards the local source
upload on success, and sets the job's terminal status. Mirrors
`worker/worker/orchestrator.py`'s own shape and error-handling philosophy.

Kept free of any assist/boto3-specific error handling beyond what's already
exposed through the `S3Client`/`VideoCutter` seams, so this module — and its
tests — never needs the real S3 bucket or `assist`'s ffmpeg/scipy stack.
"""

import logging
import shutil
from pathlib import Path

from app.models.cutting_job import (
    CuttingJob,
    CuttingJobOutput,
    CuttingJobOutputStatus,
    CuttingJobStatus,
)
from app.services.cutting_jobs import claim_next_queued_cutting_job, finalize_cutting_job
from app.services.s3_client import S3Client
from sqlalchemy.orm import Session

from assist.output_filename import OutputFilename
from assist.phase import Phase
from cutting_worker.video_cutter import VideoCutter

logger = logging.getLogger(__name__)

# Short, user-safe strings only (cutting_job_outputs.failure_reason) — same
# bounding rule as worker/orchestrator.py's own failure_reason constants:
# never a raw exception, stack trace, or file path.
_PIPELINE_FAILURE_REASON = "The cutting process did not produce a result for this phase."
_BATCH_FAILURE_REASON = "The cutting job could not be completed."


def process_next_job(
    db: Session,
    *,
    s3_client: S3Client,
    video_cutter: VideoCutter,
    work_root: Path,
) -> bool:
    """Claim and fully process the single oldest queued CuttingJob, if any.
    Returns False when the queue is empty, so the caller's poll loop knows
    whether to sleep before trying again — this never blocks waiting for
    work itself.
    """
    job = claim_next_queued_cutting_job(db)
    if job is None:
        return False

    logger.info("cutting_job_id=%s test_id=%s claimed by cutting-worker", job.id, job.test_id)

    # Only ever holds this job's *output* Cuts before they're uploaded —
    # the source video(s) it cuts from are read directly out of wherever
    # `cutting_uploads.py` already put them (CONTEXT.md's Feature C
    # "Upload mechanics" decision: never copied, never touched here except
    # to discard on success below).
    output_dir = work_root / str(job.id) / "output"
    try:
        try:
            _run_claimed_job(
                db, job, s3_client=s3_client, video_cutter=video_cutter, output_dir=output_dir
            )
        except Exception:
            # A single job's unexpected failure (a transient S3 error during
            # upload, a dropped DB connection mid-write, ...) must fail only
            # that job, never crash the whole poll loop — same reasoning as
            # worker/orchestrator.py's own outer try/except.
            logger.exception(
                "cutting_job_id=%s test_id=%s unhandled error while processing job",
                job.id,
                job.test_id,
            )
            _fail_after_unhandled_error(db, job)
    finally:
        shutil.rmtree(work_root / str(job.id), ignore_errors=True)

    return True


def _fail_after_unhandled_error(db: Session, job: CuttingJob) -> None:
    """Best-effort recovery from an exception that escaped `_run_claimed_job`
    entirely — mirrors worker/orchestrator.py's `_fail_after_unhandled_error`:
    fails *every* output (including one already marked SUCCEEDED before the
    error hit, e.g. uploading one Cut succeeded but a later one then failed)
    and finalizes accordingly. A CuttingJobOutput with no durable, retrievable
    result is operationally a failure regardless of what its in-memory status
    said a moment earlier.

    No explicit `db.rollback()` needed first — same SQLAlchemy 2.0 auto-
    rollback-on-failed-commit reasoning as the analysis worker's version.

    If even this fails, the exception propagates and crashes the process;
    `restart: unless-stopped` plus `requeue_stuck_running_cutting_jobs` on
    the next startup is the correct fallback, not a second recovery layer.
    """
    for output in job.outputs:
        output.status = CuttingJobOutputStatus.FAILED
        output.failure_reason = _BATCH_FAILURE_REASON
        db.add(output)
    db.commit()
    finalize_cutting_job(db, job)


def _run_claimed_job(
    db: Session,
    job: CuttingJob,
    *,
    s3_client: S3Client,
    video_cutter: VideoCutter,
    output_dir: Path,
) -> None:
    source_paths: dict[str, Path] = {}
    if job.c1_source_path is not None:
        source_paths["C1"] = Path(job.c1_source_path)
    if job.c2_source_path is not None:
        source_paths["C2"] = Path(job.c2_source_path)

    try:
        video_cutter.cut(
            test_id=job.test_id,
            reference_camera=job.reference_camera,
            phase_timestamps=job.phase_timestamps,
            source_paths=source_paths,
            output_dir=output_dir,
        )
    except Exception:
        logger.exception(
            "cutting_job_id=%s test_id=%s video_cutter.cut failed before completing every output",
            job.id,
            job.test_id,
        )
        for output in job.outputs:
            output.status = CuttingJobOutputStatus.FAILED
            output.failure_reason = _BATCH_FAILURE_REASON
            db.add(output)
        db.commit()
        db.refresh(job)
        finalize_cutting_job(db, job)
        return

    for output in job.outputs:
        local_path = output_dir / _expected_output_filename(job, output)
        if local_path.is_file():
            s3_client.upload_file(local_path, f"cuts/{job.test_id}/{local_path.name}")
            output.status = CuttingJobOutputStatus.SUCCEEDED
        else:
            output.status = CuttingJobOutputStatus.FAILED
            output.failure_reason = _PIPELINE_FAILURE_REASON
        db.add(output)
    db.commit()
    db.refresh(job)

    finalized = finalize_cutting_job(db, job)
    if finalized.status == CuttingJobStatus.SUCCEEDED:
        _discard_source_uploads(job)


def _expected_output_filename(job: CuttingJob, output: CuttingJobOutput) -> str:
    """The filename `VideoCutter.cut` writes `output` under, if it produced
    it — asked of `assist`'s own `OutputFilename` (pure Python, so fine to
    import here since the #112 vendoring), where the phase's name is e.g.
    `"ME_F1"` — exactly `f"{output.condition}_{output.phase}"`. Built here
    rather than parsed back out of a directory listing."""
    return OutputFilename.get(
        job.test_id, output.camera, Phase[f"{output.condition}_{output.phase}"]
    )


def _discard_source_uploads(job: CuttingJob) -> None:
    """Delete `job`'s local source upload(s) entirely (CONTEXT.md's Feature
    C decision: "on success the local upload is discarded immediately") —
    removes each upload's whole directory (meta.json + blob, see
    app/services/cutting_uploads.py), not just the video file, so nothing of
    it lingers to count against the upload storage cap. Best-effort: a
    source directory already gone (or never there) is not an error — the
    job itself already reached its terminal `succeeded` state regardless.
    """
    for source_path in (job.c1_source_path, job.c2_source_path):
        if source_path is not None:
            shutil.rmtree(Path(source_path).parent, ignore_errors=True)
