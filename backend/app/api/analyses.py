from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import (
    get_identity,
    get_verified_identity,
    identity_rate_limit_key,
    raise_if_throttled,
)
from app.core.config import settings
from app.db.session import get_db
from app.models.analysis_job import AnalysisJob
from app.models.audit_log import AuditAction
from app.schemas.analyses import AnalysisJobOut, CreateAnalysisRequest
from app.services.analyses import (
    REPORT_FILENAME,
    AnalysisJobNotCancellableError,
    AnalysisJobNotFoundError,
    AnalysisReportNotAvailableError,
    CutsWithMultipleTestsError,
    EmptyCutSelectionError,
    InvalidCutSelectionError,
    TooManyTestsError,
    TooManyVideosError,
    cancel_analysis_job,
    create_analysis_job,
    get_analysis_job,
    get_analysis_report_key,
    list_analysis_jobs,
)
from app.services.audit_log import record_audit_event
from app.services.media_browser import TestNotFoundError
from app.services.rate_limit import RateLimiter, enforce_all, get_rate_limiter
from app.services.s3_client import S3Client, S3ObjectNotFoundError, get_s3_client
from app.services.s3_client import iter_object_range as _iter_range

# No authentication or per-caller scoping here (ticket #72): Mechatronics is
# the boundary, and analysis history is fully shared — the identity header is
# read only to attribute a new job (docs/adr/0004-...).
router = APIRouter(prefix="/analyses", tags=["analyses"])


def _record_verified_audit_event(
    db: Session, request: Request, *, action: AuditAction, target_type: str, target: str
) -> None:
    """Shared by every audit-logging call site in this file (ticket #82's
    ANALYSIS_STARTED, ticket #83's ANALYSIS_CANCELLED, ...) — logged with the
    audit log's own, more strongly verified identity (`get_verified_identity`),
    independent of `get_identity` (which only ever attributes the job itself
    — docs/adr/0004-...). `record_audit_event` never raises (it isolates its
    own failure internally) but never commits either — this always commits
    the caller's transaction afterward, so a future call site copy-pasted
    without also copying that commit can't silently drop the audit row
    (caught in review).
    """
    verified_identity, identity_verified = get_verified_identity(request)
    record_audit_event(
        db,
        identity=verified_identity,
        identity_verified=identity_verified,
        action=action,
        target_type=target_type,
        target=target,
    )
    db.commit()


@router.post("", response_model=AnalysisJobOut, status_code=status.HTTP_201_CREATED)
def create_analysis(
    request: Request,
    payload: CreateAnalysisRequest,
    identity: str | None = Depends(get_identity),
    db: Session = Depends(get_db),
    s3: S3Client = Depends(get_s3_client),
    limiter: RateLimiter = Depends(get_rate_limiter),
) -> AnalysisJob:
    """Ticket #89 / issue #88's Feature B: rate limited per identity
    (reusing rate_limit.py, same pattern as media-token issuance and
    /media/cuts/info) — a generous budget, sized so it doesn't affect normal
    single-job usage, since a job is now a more expensive thing to trigger
    repeatedly than before this feature (CONTEXT.md's Feature B decision).
    """
    result = limiter.hit(
        identity_rate_limit_key("create-analysis", identity),
        limit=settings.create_analysis_rate_limit_max_attempts_per_identity,
        window_seconds=settings.create_analysis_rate_limit_window_seconds,
    )
    raise_if_throttled(enforce_all(result))

    try:
        job = create_analysis_job(
            db,
            requested_by_identity=identity,
            test_ids=payload.test_ids,
            cut_keys=payload.cuts,
            s3=s3,
        )
    except EmptyCutSelectionError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Select at least one Cut to analyze."
        ) from None
    except InvalidCutSelectionError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="One or more selected Cuts are not valid C2 analysis input for this Test.",
        ) from None
    except CutsWithMultipleTestsError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Hand-picked cuts are only allowed when selecting exactly one Test.",
        ) from None
    except TooManyTestsError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Select at most 10 Tests per analysis.",
        ) from None
    except TooManyVideosError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The selected Tests/Cuts resolve to too many videos for one analysis.",
        ) from None
    except TestNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="One or more selected Tests were not found.",
        ) from None

    _record_verified_audit_event(
        db,
        request,
        action=AuditAction.ANALYSIS_STARTED,
        target_type="analysis_job",
        target=str(job.id),
    )
    return job


@router.get("/{analysis_id}", response_model=AnalysisJobOut)
def get_analysis(
    analysis_id: int,
    db: Session = Depends(get_db),
) -> AnalysisJob:
    try:
        return get_analysis_job(db, analysis_id=analysis_id)
    except AnalysisJobNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Analysis not found."
        ) from None


# IANA media-types registry entry for OOXML spreadsheets:
# https://www.iana.org/assignments/media-types/application/vnd.openxmlformats-officedocument.spreadsheetml.sheet
_REPORT_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.get("/{analysis_id}/report")
def download_analysis_report(
    analysis_id: int,
    request: Request,
    db: Session = Depends(get_db),
    s3: S3Client = Depends(get_s3_client),
) -> StreamingResponse:
    """Download `analysis_id`'s combined `casiop_report.xlsx` (ticket #49).

    A plain download, not the media-token pattern used by Cut streaming
    (`app/api/media_browser.py`'s `public_router`) — that pattern's
    URL-embedded-token exception (docs/adr/0002-media-access-tokens-in-url.md)
    is scoped to the media browser. The frontend triggers this via an
    ordinary `fetch`, same as every other endpoint on `router`.
    """
    try:
        key = get_analysis_report_key(db, analysis_id=analysis_id)
    except AnalysisJobNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Analysis not found."
        ) from None
    except AnalysisReportNotAvailableError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No report is available for this analysis yet.",
        ) from None

    try:
        info = s3.head_object(key)
    except S3ObjectNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Report not found."
        ) from None

    # Ticket #85 / issue #79: logged here, the actual byte-serving point —
    # unlike Cut streaming's media-token pattern, this endpoint has no
    # "reliably attributed but not proof of playback" gap (CONTEXT.md's
    # audit log design), so REPORT_DOWNLOADED is written directly once the
    # object is confirmed to exist, right before the bytes are streamed.
    _record_verified_audit_event(
        db,
        request,
        action=AuditAction.REPORT_DOWNLOADED,
        target_type="analysis_job",
        target=str(analysis_id),
    )

    body = _iter_range(s3, key, 0, info.size - 1) if info.size > 0 else iter((b"",))
    return StreamingResponse(
        body,
        media_type=_REPORT_MEDIA_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="{REPORT_FILENAME}"',
            "Content-Length": str(info.size),
        },
    )


@router.post("/{analysis_id}/cancel", response_model=AnalysisJobOut)
def cancel_analysis(
    analysis_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> AnalysisJob:
    try:
        job = cancel_analysis_job(db, analysis_id=analysis_id)
    except AnalysisJobNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Analysis not found."
        ) from None
    except AnalysisJobNotCancellableError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only a queued analysis can be cancelled.",
        ) from None

    _record_verified_audit_event(
        db,
        request,
        action=AuditAction.ANALYSIS_CANCELLED,
        target_type="analysis_job",
        target=str(job.id),
    )
    return job


@router.get("", response_model=list[AnalysisJobOut])
def list_analyses(
    test_id: str | None = Query(default=None, max_length=50),
    db: Session = Depends(get_db),
) -> list[AnalysisJob]:
    return list_analysis_jobs(db, test_id=test_id)
