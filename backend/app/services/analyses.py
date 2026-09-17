"""Analysis-job request validation and persistence (ticket #45, part of
#44). No worker yet — `create_analysis_job` only ever creates a `queued`
job and its `analysis_job_videos` rows; ticket #47 adds the worker that
actually claims and runs it.
"""

import re
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.analysis_job import (
    AnalysisJob,
    AnalysisJobStatus,
    AnalysisJobVideo,
    AnalysisJobVideoStatus,
)
from app.services.media_browser import CutNotFoundError, parse_cut_key

# DogTrace only ever processes camera C2, and only a filename matching this
# exact shape, case-insensitively (CONTEXT.md's "DogTrace integration
# boundary" decision, grounded in dogtrace/sources.py's own
# C2_VIDEO_PATTERN). Deliberately stricter than media_browser's own, more
# permissive `_CUT_FILENAME_RE` (any digit-count Test id, any extension) —
# a Cut that's valid for browsing/playback can still be the wrong shape for
# analysis input.
_DOGTRACE_C2_FILENAME_RE = re.compile(r"^T\d{3}_C2_(ME|ZE)_F\d+\.mp4$", re.IGNORECASE)


class EmptyCutSelectionError(Exception):
    """Raised when a request selects zero Cuts — there's nothing to analyze.
    (Pydantic's `min_length=1` on the request already rejects this at the
    schema layer; this only guards direct callers of `create_analysis_job`.)
    """


class InvalidCutSelectionError(Exception):
    """Raised when a requested Cut key isn't valid C2 analysis input for the
    given Test: malformed key, belongs to a different Test, or its filename
    doesn't match DogTrace's own C2 pattern. Raised for the whole request
    (see `create_analysis_job`) so no job — partial or otherwise — is ever
    created for an invalid selection.

    This only checks *shape* — it never checks the Cut actually exists in
    S3. That's the worker's job (ticket #47) when it downloads them.
    """

    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(key)


class AnalysisJobNotFoundError(Exception):
    """Raised for a nonexistent AnalysisJob id, or one that exists but
    belongs to a different Account (see `get_analysis_job`) — the two are
    deliberately indistinguishable to the caller, same "404 either way"
    reasoning as CutNotFoundError, so a request can never be used to probe
    which ids exist for another Account (issue #44's "no cross-user
    visibility" decision)."""


def _validate_cut_key(test_id: str, key: str) -> None:
    try:
        filename = parse_cut_key(key)
    except CutNotFoundError:
        raise InvalidCutSelectionError(key) from None

    # parse_cut_key only guarantees the general "cuts/<test_id>/<filename>"
    # shape — it doesn't check that <test_id> matches the Test this request
    # is for. Reconstructing the expected key pins that down without a
    # second regex: parse_cut_key's own shape check already rules out a
    # nested path, so this equality is exact, not just a prefix match.
    if key != f"cuts/{test_id}/{filename}":
        raise InvalidCutSelectionError(key)

    if not _DOGTRACE_C2_FILENAME_RE.match(filename):
        raise InvalidCutSelectionError(key)


def create_analysis_job(
    db: Session, *, requested_by: int, test_id: str, cut_keys: list[str]
) -> AnalysisJob:
    """Create a `queued` AnalysisJob requesting analysis of `cut_keys`
    within `test_id`.

    Validates every key before creating anything (see `_validate_cut_key`)
    — the whole request is rejected and no job is created if any key fails,
    never a partial job (issue #44's S3 input flow, step 1).
    """
    if not cut_keys:
        raise EmptyCutSelectionError

    for key in cut_keys:
        _validate_cut_key(test_id, key)

    job = AnalysisJob(test_id=test_id, requested_by=requested_by)
    job.videos = [
        AnalysisJobVideo(cut_key=key, position=position, status=AnalysisJobVideoStatus.PENDING)
        for position, key in enumerate(cut_keys)
    ]
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def get_analysis_job(db: Session, *, requested_by: int, analysis_id: int) -> AnalysisJob:
    """The AnalysisJob for `analysis_id`, scoped to `requested_by` — every
    Account may only ever see its own jobs (issue #44's "Authorization"
    section: "No cross-user visibility into other Accounts' analyses in
    v1", Admin included)."""
    job = db.scalar(
        select(AnalysisJob).where(
            AnalysisJob.id == analysis_id, AnalysisJob.requested_by == requested_by
        )
    )
    if job is None:
        raise AnalysisJobNotFoundError(analysis_id)
    return job


class AnalysisJobNotCancellableError(Exception):
    """Raised when cancelling an AnalysisJob that isn't `queued` — a
    `running` job cannot be cancelled in v1 (issue #44's "Functional
    requirements": "A queued (not yet started) job can be cancelled. A
    running job cannot be cancelled in v1."), and cancelling an already-
    terminal job (`completed`/`completed_with_errors`/`failed`/`cancelled`)
    makes no sense either."""


def cancel_analysis_job(db: Session, *, requested_by: int, analysis_id: int) -> AnalysisJob:
    """Cancel `requested_by`'s own `queued` AnalysisJob `analysis_id`.

    Raises AnalysisJobNotFoundError for a nonexistent id or one owned by a
    different Account (same scoping as `get_analysis_job`), and
    AnalysisJobNotCancellableError if it's not currently `queued` — in
    particular, a `running` job is left untouched, not cancelled out from
    under the worker processing it. A `queued` job never had a temp
    directory created (issue #44's "S3 input flow" step 5), so there's
    nothing to clean up here.

    `SELECT ... FOR UPDATE` rather than plain `get_analysis_job`: this is a
    read-then-write against the same row the worker (ticket #47) claims via
    its own `SELECT ... FOR UPDATE SKIP LOCKED`. Locking here means the two
    can never race to completion on the same row — either this transaction
    commits the cancellation first (the worker's claim then simply finds no
    queued row), or the worker's claim commits first (this blocks until it
    does, then sees `status=running` and correctly refuses to cancel).
    """
    job = db.scalar(
        select(AnalysisJob)
        .where(AnalysisJob.id == analysis_id, AnalysisJob.requested_by == requested_by)
        .with_for_update()
    )
    if job is None:
        raise AnalysisJobNotFoundError(analysis_id)
    if job.status != AnalysisJobStatus.QUEUED:
        raise AnalysisJobNotCancellableError(job.status)

    job.status = AnalysisJobStatus.CANCELLED
    # Cancellation is a terminal state (issue #44's "Job state machine":
    # `queued` -> `cancelled` alongside the `running` -> {completed, ...}
    # terminal transitions) — finished_at marks *any* terminal state, not
    # just a worker-run one, so callers can't tell "cancelled" and
    # "never finished" apart by a null finished_at.
    job.finished_at = datetime.now(UTC)
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def claim_next_queued_job(db: Session, *, dogtrace_version: str) -> AnalysisJob | None:
    """Atomically claim the oldest still-`queued` AnalysisJob for the
    worker (ticket #47) to run, or None if the queue is empty.

    `SELECT ... FOR UPDATE SKIP LOCKED` (issue #44's "Queue: Postgres
    itself" decision) — with exactly one worker process this never
    actually contends, but it's what makes the design safe to later run
    more than one worker without double-processing a job, and it's also
    the row lock `cancel_analysis_job` documents racing against: either
    that transaction commits the cancellation first (this then simply
    finds no queued row) or this commits the claim first (cancel then
    sees `status=running` and correctly refuses).

    Sets `status=running`, `started_at`, and `dogtrace_version` as part of
    the same claim (ticket #47: "On claiming a job: status -> running,
    started_at set, dogtrace_version recorded") so a claimed job is never
    observably `queued` with no owner.
    """
    job = db.scalar(
        select(AnalysisJob)
        .where(AnalysisJob.status == AnalysisJobStatus.QUEUED)
        .order_by(AnalysisJob.created_at.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if job is None:
        return None

    job.status = AnalysisJobStatus.RUNNING
    job.started_at = datetime.now(UTC)
    job.dogtrace_version = dogtrace_version
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def finalize_analysis_job(
    db: Session, job: AnalysisJob, *, report_s3_prefix: str | None
) -> AnalysisJob:
    """Set `job`'s terminal status from its videos' already-recorded final
    statuses (issue #44's job state machine): `completed` if every video
    succeeded, `completed_with_errors` if at least one succeeded and at
    least one failed, `failed` if none succeeded (including a job that
    errored before any video could be attempted, per ticket #47 — the
    caller marks every such video `failed` before calling this).

    Call only after any produced artifacts are already durably uploaded —
    `report_s3_prefix` (and thus `AnalysisJob.report_available`) must never
    point at an S3 prefix that doesn't actually hold anything yet.
    """
    video_statuses = {video.status for video in job.videos}
    if video_statuses <= {AnalysisJobVideoStatus.SUCCEEDED}:
        job.status = AnalysisJobStatus.COMPLETED
    elif AnalysisJobVideoStatus.SUCCEEDED in video_statuses:
        job.status = AnalysisJobStatus.COMPLETED_WITH_ERRORS
    else:
        job.status = AnalysisJobStatus.FAILED

    job.report_s3_prefix = report_s3_prefix
    job.finished_at = datetime.now(UTC)
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def requeue_stuck_running_jobs(db: Session) -> list[AnalysisJob]:
    """Reset every AnalysisJob still `running` back to `queued` (and its
    videos back to `pending`, clearing any failure_reason), returning the
    jobs that were reset.

    Meant to be called once, before the worker (ticket #47) starts polling.
    Exactly one worker instance ever processes jobs (issue #44's
    concurrency strategy), so an AnalysisJob still `running` at worker
    startup can only mean a previous worker process crashed or was
    restarted mid-job, never a different, still-active worker legitimately
    owning it. Requeuing (rather than marking `failed`) means the user's
    request is retried automatically instead of silently lost — see
    CONTEXT.md's "Analysis worker" decision on this choice; issue #44
    explicitly left the exact policy open. The caller is responsible for
    removing each returned job's now-orphaned temp directory, since this
    module has no opinion on worker filesystem layout.
    """
    jobs = list(
        db.scalars(select(AnalysisJob).where(AnalysisJob.status == AnalysisJobStatus.RUNNING))
    )
    for job in jobs:
        job.status = AnalysisJobStatus.QUEUED
        job.started_at = None
        job.dogtrace_version = None
        db.add(job)
        for video in job.videos:
            video.status = AnalysisJobVideoStatus.PENDING
            video.failure_reason = None
            db.add(video)
    if jobs:
        db.commit()
    return jobs


def list_analysis_jobs(
    db: Session, *, requested_by: int, test_id: str | None = None
) -> list[AnalysisJob]:
    """`requested_by`'s own AnalysisJobs, newest first, optionally narrowed
    to one Test — backs both the global history page and the Media
    Browser's inline "previous analyses for this Test" panel (ticket #53)."""
    stmt = select(AnalysisJob).where(AnalysisJob.requested_by == requested_by)
    if test_id is not None:
        stmt = stmt.where(AnalysisJob.test_id == test_id)
    stmt = stmt.order_by(AnalysisJob.created_at.desc())
    return list(db.scalars(stmt))
