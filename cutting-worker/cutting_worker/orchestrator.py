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
from collections.abc import Callable
from pathlib import Path

from app.models.audit_log import AuditAction
from app.models.cutting_job import (
    CuttingJob,
    CuttingJobOutput,
    CuttingJobOutputStatus,
    CuttingJobStatus,
)
from app.services.audit_log import record_audit_event
from app.services.cutting_jobs import (
    AUDIT_TARGET_TYPE,
    claim_next_queued_cutting_job,
    finalize_cutting_job,
)
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
    entirely (e.g. an S3 upload failing during the post-run sweep), then
    finalizes accordingly.

    Fails only what's still `pending`. An output is only ever marked
    SUCCEEDED by `_upload_output`, straight after its Cut's S3 upload
    returned, so every `succeeded` output — committed live (ticket #98) or
    still only in memory from the post-run sweep — has a durable,
    retrievable Cut and keeps that status. (The analysis worker's version
    fails every output instead.) No `db.rollback()` first — same SQLAlchemy
    2.0 auto-rollback-on-failed-commit reasoning as the analysis worker's
    version.

    If even this fails, the exception propagates and crashes the process;
    `restart: unless-stopped` plus `requeue_stuck_running_cutting_jobs` on
    the next startup is the correct fallback, not a second recovery layer.
    """
    _fail_pending_outputs(db, job, _BATCH_FAILURE_REASON)
    _finalize_and_audit_job(db, job)


def _fail_pending_outputs(db: Session, job: CuttingJob, failure_reason: str) -> None:
    """Mark every still-`pending` output failed and commit. An output
    already `succeeded` was uploaded and committed, so it keeps that status
    even though the job as a whole now fails."""
    for output in job.outputs:
        if output.status == CuttingJobOutputStatus.PENDING:
            output.status = CuttingJobOutputStatus.FAILED
            output.failure_reason = failure_reason
            db.add(output)
    db.commit()
    db.refresh(job)


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

    outputs_by_filename = {_expected_output_filename(job, output): output for output in job.outputs}

    try:
        video_cutter.cut(
            test_id=job.test_id,
            reference_camera=job.reference_camera,
            phase_timestamps=job.phase_timestamps,
            source_paths=source_paths,
            output_dir=output_dir,
            on_output_written=_make_output_written_callback(
                db, job, outputs_by_filename, s3_client=s3_client
            ),
        )
    except Exception:
        logger.exception(
            "cutting_job_id=%s test_id=%s video_cutter.cut failed before completing every output",
            job.id,
            job.test_id,
        )
        _fail_pending_outputs(db, job, _BATCH_FAILURE_REASON)
        _finalize_and_audit_job(db, job)
        return

    # Fallback sweep of the output directory, for anything the cutter wrote
    # without reporting it; whatever's still pending after that was never
    # produced. Failures are only knowable here, once the cutter is done —
    # a missing file is an absence, not an event to report.
    for filename, output in outputs_by_filename.items():
        local_path = output_dir / filename
        if output.status == CuttingJobOutputStatus.PENDING and local_path.is_file():
            _upload_output(db, job, output, local_path, s3_client=s3_client)
    _fail_pending_outputs(db, job, _PIPELINE_FAILURE_REASON)

    finalized = _finalize_and_audit_job(db, job)
    if finalized.status == CuttingJobStatus.SUCCEEDED:
        _discard_source_uploads(job)


def _finalize_and_audit_job(db: Session, job: CuttingJob) -> CuttingJob:
    """Finalize `job` (`finalize_cutting_job`) and write the matching
    CUTTING_COMPLETED / CUTTING_FAILED audit row (ticket #99) — the one seam
    every finalize path goes through, so none can reach a terminal status
    without its audit row. Mirrors worker/orchestrator.py's
    `_finalize_and_audit_job`:

    - identity is the job's own `requested_by_identity`, with
      `identity_verified=False`: there's no live request here to verify a
      JWT against;
    - the audit write and its commit are best-effort and never propagate.
      The job's real outcome is already committed by then, so an audit
      failure must never reach `process_next_job`'s catch-all recovery,
      which would re-fail an already-succeeded job (and keep its source
      upload instead of discarding it).
    """
    job = finalize_cutting_job(db, job)

    if job.status == CuttingJobStatus.SUCCEEDED:
        action = AuditAction.CUTTING_COMPLETED
        failure_reason = None
    else:
        failed_count = sum(
            1 for output in job.outputs if output.status == CuttingJobOutputStatus.FAILED
        )
        action = AuditAction.CUTTING_FAILED
        failure_reason = f"{failed_count} of {len(job.outputs)} Cuts failed."

    try:
        record_audit_event(
            db,
            identity=job.requested_by_identity,
            identity_verified=False,
            action=action,
            target_type=AUDIT_TARGET_TYPE,
            target=str(job.id),
            failure_reason=failure_reason,
        )
        db.commit()
    except Exception:
        logger.exception(
            "cutting_job_id=%s failed to commit %s audit event after finalizing the job",
            job.id,
            action,
        )

    return job


def _make_output_written_callback(
    db: Session,
    job: CuttingJob,
    outputs_by_filename: dict[str, CuttingJobOutput],
    *,
    s3_client: S3Client,
) -> Callable[[Path], None]:
    """Build the `on_output_written` callback passed into `VideoCutter.cut`
    (ticket #98's live per-phase progress; mirrors worker/orchestrator.py's
    `_make_progress_callback`). Each reported Cut is uploaded and its
    CuttingJobOutput marked succeeded — and committed, so
    `GET /cutting-jobs/{id}` sees it — while the job is still `running`.

    A file no output expects is logged and ignored; a repeat report for an
    output no longer `pending` is ignored. An upload error propagates out of
    `cut()` (see `VideoCutter.cut`) and fails the rest of the job.
    """

    def on_output_written(local_path: Path) -> None:
        output = outputs_by_filename.get(local_path.name)
        if output is None:
            logger.warning(
                "cutting_job_id=%s ignoring unexpected output file %s", job.id, local_path.name
            )
            return
        if output.status != CuttingJobOutputStatus.PENDING:
            return
        _upload_output(db, job, output, local_path, s3_client=s3_client)
        db.commit()

    return on_output_written


def _upload_output(
    db: Session,
    job: CuttingJob,
    output: CuttingJobOutput,
    local_path: Path,
    *,
    s3_client: S3Client,
) -> None:
    """Upload one produced Cut to `cuts/<Test>/` and mark its output
    succeeded — the caller commits."""
    s3_client.upload_file(local_path, f"cuts/{job.test_id}/{local_path.name}")
    output.status = CuttingJobOutputStatus.SUCCEEDED
    db.add(output)


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
