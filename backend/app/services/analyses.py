"""Analysis-job request validation and persistence (ticket #45, part of
#44). No worker yet — `create_analysis_job` only ever creates a `queued`
job and its `analysis_job_videos` rows; ticket #47 adds the worker that
actually claims and runs it.
"""

import re
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.analysis_job import (
    AnalysisJob,
    AnalysisJobStatus,
    AnalysisJobTest,
    AnalysisJobVideo,
    AnalysisJobVideoStatus,
)
from app.services.media_browser import CutNotFoundError, list_cuts_for_test, parse_cut_key
from app.services.s3_client import S3Client

# DogTrace only ever processes camera C2, and only a filename matching this
# exact shape, case-insensitively (CONTEXT.md's "DogTrace integration
# boundary" decision, grounded in dogtrace/sources.py's own
# C2_VIDEO_PATTERN). Deliberately stricter than media_browser's own, more
# permissive `_CUT_FILENAME_RE` (any digit-count Test id, any extension) —
# a Cut that's valid for browsing/playback can still be the wrong shape for
# analysis input.
_DOGTRACE_C2_FILENAME_RE = re.compile(r"^T\d{3}_C2_(ME|ZE)_F\d+\.mp4$", re.IGNORECASE)

# The only artifact exposed as a download in v1 (issue #44's "Report
# persistence" decision) — every other file the worker uploads alongside it
# under the same `report_s3_prefix` (track_report.xlsx/csv, .pkl files,
# config.json, trace images) stays un-surfaced.
REPORT_FILENAME = "casiop_report.xlsx"

# Ticket #89 / issue #88's Feature B limits: 10 Tests per job, 300 total
# videos per job (backstop) — a fully-populated Test structurally tops out
# at 16 C2 videos, so 10 Tests caps out around 160 in the normal case; 300
# is headroom insurance, not an expected ceiling (CONTEXT.md's Feature B
# decision). Both are backstops over CreateAnalysisRequest's own schema-level
# `test_ids` bound — re-checked here for a direct caller of
# `create_analysis_job` that bypasses the schema, same reasoning as
# EmptyCutSelectionError below.
MAX_TESTS_PER_JOB = 10
MAX_VIDEOS_PER_JOB = 300


class EmptyCutSelectionError(Exception):
    """Raised when a request resolves to zero Cuts to analyze — either an
    explicit empty selection, or wholesale derivation finding no
    C2-eligible Cut across every listed Test. (Pydantic's `min_length=1` on
    the request's `test_ids`/`cuts` already rejects the obvious cases at the
    schema layer; this also covers a direct caller of `create_analysis_job`,
    and wholesale derivation genuinely finding nothing.)
    """


class TooManyTestsError(Exception):
    """Raised when a request lists more than `MAX_TESTS_PER_JOB` Tests."""

    def __init__(self, count: int) -> None:
        self.count = count
        super().__init__(count)


class TooManyVideosError(Exception):
    """Raised when the resolved video count (wholesale-derived or, for the
    single-Test case, explicitly selected) exceeds `MAX_VIDEOS_PER_JOB`."""

    def __init__(self, count: int) -> None:
        self.count = count
        super().__init__(count)


class CutsWithMultipleTestsError(Exception):
    """Raised when `cuts` is given alongside more than one `test_id` —
    hand-picking individual Cuts stays available only when exactly one Test
    is selected (CONTEXT.md's Feature B "Selection is wholesale for
    multi-Test, per-video for single-Test" decision); wholesale (`cuts`
    omitted) is the only mode once more than one Test is listed."""


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
    """Raised for a nonexistent AnalysisJob id. Every job is visible to
    everyone since ticket #72 (analysis history is fully shared), so "not
    found" now only ever means "no such id"."""


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


def _wholesale_cut_keys(s3: S3Client, test_id: str) -> list[str]:
    """Every C2-eligible Cut key for `test_id` — wholesale selection
    (CONTEXT.md's Feature B decision), reusing `media_browser.py`'s existing
    Cut-listing service and the same `_DOGTRACE_C2_FILENAME_RE` an explicit
    selection is validated against, rather than a second, parallel
    Cut-discovery path. Propagates `TestNotFoundError` (from
    `list_cuts_for_test`) for an unknown Test id.
    """
    cuts = list_cuts_for_test(s3, test_id)
    return [cut.key for cut in cuts if _DOGTRACE_C2_FILENAME_RE.match(cut.filename)]


def create_analysis_job(
    db: Session,
    *,
    requested_by_identity: str | None,
    test_ids: list[str],
    cut_keys: list[str] | None = None,
    s3: S3Client | None = None,
) -> AnalysisJob:
    """Create a `queued` AnalysisJob spanning `test_ids` (1-10 Tests),
    attributed to `requested_by_identity` (the identity Mechatronics
    forwarded, or None — attribution only, never authorization).

    `cut_keys` given (hand-picked selection) is only valid when `test_ids`
    has exactly one entry — raises CutsWithMultipleTestsError otherwise.
    `cut_keys` omitted (None) means wholesale: every C2-eligible Cut across
    every listed Test is derived server-side via `s3` (required in that
    case — every real caller, the API endpoint included, always has one).

    Validates/resolves every key before creating anything — the whole
    request is rejected and no job is created if any key is invalid, the
    resolved video count exceeds `MAX_VIDEOS_PER_JOB`, or `test_ids` exceeds
    `MAX_TESTS_PER_JOB`, never a partial job (issue #44's S3 input flow,
    step 1, extended to the new limits by ticket #89).
    """
    if len(test_ids) > MAX_TESTS_PER_JOB:
        raise TooManyTestsError(len(test_ids))

    # A caller listing the same Test twice is nonsensical (there's only one
    # of it to select) rather than a real multi-Test request — deduped here,
    # preserving submission order, before the "cuts needs exactly one Test"
    # check below, so a duplicated test_id alongside cuts isn't wrongly
    # rejected as multi-Test. Also keeps this from ever tripping the
    # (analysis_id, test_id) uniqueness constraint below.
    unique_test_ids = list(dict.fromkeys(test_ids))

    if cut_keys is not None and len(unique_test_ids) > 1:
        raise CutsWithMultipleTestsError

    if cut_keys is not None:
        resolved_cut_keys = list(cut_keys)
        for key in resolved_cut_keys:
            _validate_cut_key(unique_test_ids[0], key)
    else:
        if s3 is None:
            raise ValueError("s3 is required for wholesale (cuts=None) derivation")
        resolved_cut_keys = []
        for test_id in unique_test_ids:
            resolved_cut_keys.extend(_wholesale_cut_keys(s3, test_id))

    if not resolved_cut_keys:
        raise EmptyCutSelectionError
    if len(resolved_cut_keys) > MAX_VIDEOS_PER_JOB:
        raise TooManyVideosError(len(resolved_cut_keys))

    job = AnalysisJob(requested_by_identity=requested_by_identity)
    job.tests = [AnalysisJobTest(test_id=test_id) for test_id in unique_test_ids]
    job.videos = [
        AnalysisJobVideo(cut_key=key, position=position, status=AnalysisJobVideoStatus.PENDING)
        for position, key in enumerate(resolved_cut_keys)
    ]
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def get_analysis_job(db: Session, *, analysis_id: int) -> AnalysisJob:
    """The AnalysisJob for `analysis_id`, whoever requested it — analysis
    history is fully shared (ticket #72 superseding issue #44's "no
    cross-user visibility" decision; docs/adr/0004-...)."""
    job = db.get(AnalysisJob, analysis_id)
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


def cancel_analysis_job(db: Session, *, analysis_id: int) -> AnalysisJob:
    """Cancel the `queued` AnalysisJob `analysis_id`, whoever requested it.

    Raises AnalysisJobNotFoundError for a nonexistent id, and
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
    job = db.scalar(select(AnalysisJob).where(AnalysisJob.id == analysis_id).with_for_update())
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


class AnalysisReportNotAvailableError(Exception):
    """Raised when `analysis_id`'s job hasn't reached a terminal state with
    at least one succeeded video (ticket #49's acceptance criteria).
    Equivalent to checking `job.status not in {COMPLETED,
    COMPLETED_WITH_ERRORS}` — `finalize_analysis_job` only ever reaches one
    of those two statuses when at least one video succeeded, so no separate
    per-video count is needed here."""


def get_analysis_report_key(db: Session, *, analysis_id: int) -> str:
    """The S3 key of `analysis_id`'s combined report (ticket #49), or raise
    if it isn't downloadable yet.

    Raises AnalysisJobNotFoundError for a nonexistent id, and
    AnalysisReportNotAvailableError for a job not
    yet in a terminal state with an uploaded report — `report_s3_prefix`
    is checked directly (rather than trusting `status` alone) since it's
    the one field `finalize_analysis_job` guarantees is only set once
    something was actually uploaded.
    """
    job = get_analysis_job(db, analysis_id=analysis_id)
    if (
        job.status not in (AnalysisJobStatus.COMPLETED, AnalysisJobStatus.COMPLETED_WITH_ERRORS)
        or job.report_s3_prefix is None
    ):
        raise AnalysisReportNotAvailableError(job.status)
    return f"{job.report_s3_prefix}{REPORT_FILENAME}"


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


# Every job is visible to everyone since ticket #72, so an unscoped listing
# grows without bound as history accumulates — capped to the newest jobs
# (ENGINEERING-STANDARDS.md §5: avoid unbounded database queries). Generous
# for a research group's shared history; add real pagination if it's ever hit.
MAX_LISTED_ANALYSIS_JOBS = 500


def list_analysis_jobs(
    db: Session, *, test_id: str | None = None, limit: int = MAX_LISTED_ANALYSIS_JOBS
) -> list[AnalysisJob]:
    """The newest AnalysisJobs (up to `limit`), whoever requested them,
    optionally narrowed to one Test — backs both the global history page
    and the Media Browser's inline "previous analyses for this Test" panel
    (ticket #53), which a multi-Test job (ticket #89) now appears in for
    *every* Test it touches, not just one.

    Each job's videos and Test association are loaded up front
    (`selectinload`): every job in the response serializes them, so lazy
    loading would be one extra query per row, per relationship.
    """
    stmt = select(AnalysisJob).options(
        selectinload(AnalysisJob.videos), selectinload(AnalysisJob.tests)
    )
    if test_id is not None:
        stmt = stmt.join(AnalysisJobTest).where(AnalysisJobTest.test_id == test_id)
    stmt = stmt.order_by(AnalysisJob.created_at.desc(), AnalysisJob.id.desc()).limit(limit)
    return list(db.scalars(stmt))
