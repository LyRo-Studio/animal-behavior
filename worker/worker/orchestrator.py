"""Claims and fully processes one AnalysisJob (ticket #47, part of #44):
download its selected Cuts, run them through DogTrace, upload whatever
artifacts were produced, set the job's terminal state, and clean up its
job-scoped temp directory. See CONTEXT.md's "Analysis worker" decisions.

Kept free of any dogtrace/boto3-specific error handling beyond what's
already exposed through the `S3Client`/`DogTraceRunner` seams, so this
module — and its tests — never needs the real S3 bucket or the real,
GPU-dependent `dogtrace` package.
"""

import logging
import shutil
from pathlib import Path

from app.models.analysis_job import AnalysisJob, AnalysisJobVideo, AnalysisJobVideoStatus
from app.services.analyses import claim_next_queued_job, finalize_analysis_job
from app.services.s3_client import S3Client, S3ObjectNotFoundError
from sqlalchemy.orm import Session

from worker.dogtrace_runner import DogTraceRunner

logger = logging.getLogger(__name__)

# Short, user-safe strings only (analysis_job_videos.failure_reason) — see
# issue #44's "Failure handling" decision: never a raw exception, stack
# trace, or file path. Full detail always goes to logger.exception/warning
# below instead, correlated with analysis_id/account_id/test_id/cut_key.
_DOWNLOAD_FAILURE_REASON = "The source video could not be retrieved from storage."
_PIPELINE_FAILURE_REASON = "The analysis did not produce a result for this video."
_BATCH_FAILURE_REASON = "The analysis could not be completed for this video."


def process_next_job(
    db: Session,
    *,
    s3_client: S3Client,
    dogtrace_runner: DogTraceRunner,
    work_root: Path,
) -> bool:
    """Claim and fully process the single oldest queued AnalysisJob, if
    any. Returns False when the queue is empty, so the caller's poll loop
    knows whether to sleep before trying again — this never blocks waiting
    for work itself.
    """
    job = claim_next_queued_job(db, dogtrace_version=dogtrace_runner.version)
    if job is None:
        return False

    logger.info(
        "analysis_id=%s account_id=%s test_id=%s claimed by worker",
        job.id,
        job.requested_by,
        job.test_id,
    )

    job_dir = work_root / str(job.id)
    try:
        try:
            _run_claimed_job(
                db,
                job,
                s3_client=s3_client,
                dogtrace_runner=dogtrace_runner,
                input_dir=job_dir / "input",
                output_dir=job_dir / "output",
            )
        except Exception:
            # A single job's unexpected failure (a transient S3 error
            # during upload, a dropped DB connection mid-write, ...) must
            # fail only that job, never crash the whole poll loop — an
            # unhandled exception here would otherwise kill the worker
            # process on every single job that happens to hit it, and if
            # the same job deterministically retriggers the same error
            # after being requeued on restart, crash-loop forever without
            # ever reaching the next queued job.
            logger.exception(
                "analysis_id=%s account_id=%s test_id=%s unhandled error while processing job",
                job.id,
                job.requested_by,
                job.test_id,
            )
            _fail_after_unhandled_error(db, job)
    finally:
        # Never leave a job-scoped temp dir on disk once the job reaches a
        # terminal state (issue #44's cleanup strategy) — regardless of
        # which terminal status was reached. Artifacts are already durably
        # uploaded to S3 by this point (or there were none to upload); this
        # only removes the now-redundant local copy.
        shutil.rmtree(job_dir, ignore_errors=True)

    return True


def _fail_after_unhandled_error(db: Session, job: AnalysisJob) -> None:
    """Best-effort recovery from an exception that escaped `_run_claimed_job`
    entirely: fail *every* video — including one already marked SUCCEEDED
    before the error hit, e.g. the exact scenario this recovers from: a
    video's own pipeline run finishes, but uploading its result to S3 then
    fails — and finalize the job accordingly, with no report prefix. A
    video with no durable, retrievable result is operationally a failure
    regardless of what its in-memory status said a moment earlier; nothing
    here can prove what (if anything) actually made it to S3. No explicit
    `db.rollback()` needed first: SQLAlchemy 2.0's Session already rolls
    back automatically if a `commit()` itself was what failed, leaving `db`
    usable again immediately.

    If even this fails (e.g. the database itself is unreachable), the
    exception is left to propagate and crash the process — `restart:
    unless-stopped` plus `requeue_stuck_running_jobs` on the next startup is
    the correct fallback for that case, not a second layer of recovery here.
    """
    for video in job.videos:
        video.status = AnalysisJobVideoStatus.FAILED
        video.failure_reason = _BATCH_FAILURE_REASON
        db.add(video)
    db.commit()
    finalize_analysis_job(db, job, report_s3_prefix=None)


def _run_claimed_job(
    db: Session,
    job: AnalysisJob,
    *,
    s3_client: S3Client,
    dogtrace_runner: DogTraceRunner,
    input_dir: Path,
    output_dir: Path,
) -> None:
    local_paths: list[Path] = []
    for video in job.videos:
        local_path = _download_video(s3_client, job, video, input_dir)
        if local_path is not None:
            local_paths.append(local_path)

    if local_paths:
        # `create_analysis_job` (ticket #45) doesn't reject a request that
        # names the same Cut key twice — dedupe here so a video is never
        # handed to `run_reporting` more than once (wasted GPU work, and an
        # untested input shape for DogTrace itself). Every AnalysisJobVideo
        # row, duplicates included, still gets its own status below from
        # whether *its* video_stem produced output.
        unique_local_paths = list(dict.fromkeys(local_paths))
        try:
            dogtrace_runner.run_reporting(unique_local_paths, output_dir=output_dir)
        except Exception:
            logger.exception(
                "analysis_id=%s account_id=%s test_id=%s run_reporting failed "
                "before completing every video",
                job.id,
                job.requested_by,
                job.test_id,
            )
            # Coarse by design (ticket #47: no progress callback yet) — a
            # video already resolved (e.g. a download failure above) keeps
            # its own specific reason; everything still `pending` when the
            # whole call blew up gets this generic one instead of being
            # left stuck.
            for video in job.videos:
                if video.status == AnalysisJobVideoStatus.PENDING:
                    video.status = AnalysisJobVideoStatus.FAILED
                    video.failure_reason = _BATCH_FAILURE_REASON
                    db.add(video)
        else:
            for video in job.videos:
                if video.status != AnalysisJobVideoStatus.PENDING:
                    continue
                if _video_produced_output(output_dir, Path(video.cut_key).stem):
                    video.status = AnalysisJobVideoStatus.SUCCEEDED
                else:
                    video.status = AnalysisJobVideoStatus.FAILED
                    video.failure_reason = _PIPELINE_FAILURE_REASON
                db.add(video)

    db.commit()
    db.refresh(job)

    report_s3_prefix = _upload_artifacts(s3_client, output_dir, job)
    finalize_analysis_job(db, job, report_s3_prefix=report_s3_prefix)


def _download_video(
    s3_client: S3Client, job: AnalysisJob, video: AnalysisJobVideo, input_dir: Path
) -> Path | None:
    """Download `video`'s Cut into `input_dir`, preserving its original S3
    filename (required by `reporting_v24.py`'s `parse_video_filename`).

    A missing object here is a per-video failure, not a whole-job one — Cut
    keys are only checked for *shape* at request time (see
    `InvalidCutSelectionError`'s docstring), never for actually existing in
    S3, so a Cut deleted between request and processing is expected here,
    not exceptional. Every other video in the job still gets attempted.
    """
    local_path = input_dir / Path(video.cut_key).name
    try:
        s3_client.download_file(video.cut_key, local_path)
    except S3ObjectNotFoundError:
        logger.warning(
            "analysis_id=%s account_id=%s test_id=%s cut_key=%s not found in storage",
            job.id,
            job.requested_by,
            job.test_id,
            video.cut_key,
        )
        video.status = AnalysisJobVideoStatus.FAILED
        video.failure_reason = _DOWNLOAD_FAILURE_REASON
        return None
    return local_path


def _video_produced_output(output_dir: Path, video_stem: str) -> bool:
    """Whether DogTrace produced a per-video result for `video_stem`.

    There's no return value or progress callback from `run_reporting` yet
    that reports this directly (ticket #47's "coarse status... based on
    which output artifacts exist per video"). `dogtrace.reporting_v24`'s
    `VideoReport.from_video` creates `output_dir/<video_stem>/<timestamp>/`
    up front, before that video is even attempted, so the directory's mere
    existence can't distinguish success from failure — `track_report.xlsx`
    is only ever written by `VideoReport.run()` once that video's pipeline
    has actually finished, so its presence is the real per-video completion
    marker.
    """
    return any(output_dir.glob(f"{video_stem}/*/track_report.xlsx"))


def _upload_artifacts(s3_client: S3Client, output_dir: Path, job: AnalysisJob) -> str | None:
    """Upload every file under `output_dir` to `reports/<test_id>/<id>/`
    (issue #44's report-persistence decision), preserving each file's path
    relative to `output_dir` — not just `casiop_report.xlsx`.

    Returns that prefix, or None if nothing was produced to upload (e.g.
    every video failed before any output was written, or downloading
    failed for every requested Cut) — `report_s3_prefix`/
    `AnalysisJob.report_available` must never point at an S3 prefix with
    nothing in it.
    """
    if not output_dir.exists():
        return None

    prefix = f"reports/{job.test_id}/{job.id}/"
    uploaded_anything = False
    for local_path in sorted(output_dir.rglob("*")):
        if not local_path.is_file():
            continue
        relative_key = local_path.relative_to(output_dir).as_posix()
        s3_client.upload_file(local_path, f"{prefix}{relative_key}")
        uploaded_anything = True

    return prefix if uploaded_anything else None
