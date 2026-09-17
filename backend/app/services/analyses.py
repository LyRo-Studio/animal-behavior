"""Analysis-job request validation and persistence (ticket #45, part of
#44). No worker yet — `create_analysis_job` only ever creates a `queued`
job and its `analysis_job_videos` rows; ticket #47 adds the worker that
actually claims and runs it.
"""

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.analysis_job import AnalysisJob, AnalysisJobVideo, AnalysisJobVideoStatus
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
