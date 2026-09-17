from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_account
from app.db.session import get_db
from app.models.account import Account
from app.models.analysis_job import AnalysisJob
from app.schemas.analyses import AnalysisJobOut, CreateAnalysisRequest
from app.services.analyses import (
    REPORT_FILENAME,
    AnalysisJobNotCancellableError,
    AnalysisJobNotFoundError,
    AnalysisReportNotAvailableError,
    EmptyCutSelectionError,
    InvalidCutSelectionError,
    cancel_analysis_job,
    create_analysis_job,
    get_analysis_job,
    get_analysis_report_key,
    list_analysis_jobs,
)
from app.services.s3_client import S3Client, S3ObjectNotFoundError, get_s3_client
from app.services.s3_client import iter_object_range as _iter_range

# Every authenticated Account (User or Admin) gets identical access here —
# no extra role gating, matching the Media Browser's existing access rule
# (CONTEXT.md's "Media browser — access" decision, carried over by issue
# #44's "Authorization" section).
router = APIRouter(
    prefix="/analyses", tags=["analyses"], dependencies=[Depends(get_current_account)]
)


@router.post("", response_model=AnalysisJobOut, status_code=status.HTTP_201_CREATED)
def create_analysis(
    payload: CreateAnalysisRequest,
    account: Account = Depends(get_current_account),
    db: Session = Depends(get_db),
) -> AnalysisJob:
    try:
        return create_analysis_job(
            db, requested_by=account.id, test_id=payload.test_id, cut_keys=payload.cuts
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


@router.get("/{analysis_id}", response_model=AnalysisJobOut)
def get_analysis(
    analysis_id: int,
    account: Account = Depends(get_current_account),
    db: Session = Depends(get_db),
) -> AnalysisJob:
    try:
        return get_analysis_job(db, requested_by=account.id, analysis_id=analysis_id)
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
    account: Account = Depends(get_current_account),
    db: Session = Depends(get_db),
    s3: S3Client = Depends(get_s3_client),
) -> StreamingResponse:
    """Download `analysis_id`'s combined `casiop_report.xlsx` (ticket #49).

    A plain bearer-authenticated download, not the media-token pattern used
    by Cut streaming (`app/api/media_browser.py`'s `public_router`) — this
    endpoint is never reached by a browser's native `<video>`/download-link
    request that can't carry an `Authorization` header, so it doesn't need
    that pattern's URL-embedded-token exception (see
    docs/adr/0002-media-access-tokens-in-url.md, which is scoped to the
    media browser). The frontend triggers this via an authenticated fetch,
    same as every other bearer-authenticated endpoint on `router`.
    """
    try:
        key = get_analysis_report_key(db, requested_by=account.id, analysis_id=analysis_id)
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
    account: Account = Depends(get_current_account),
    db: Session = Depends(get_db),
) -> AnalysisJob:
    try:
        return cancel_analysis_job(db, requested_by=account.id, analysis_id=analysis_id)
    except AnalysisJobNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Analysis not found."
        ) from None
    except AnalysisJobNotCancellableError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only a queued analysis can be cancelled.",
        ) from None


@router.get("", response_model=list[AnalysisJobOut])
def list_analyses(
    test_id: str | None = Query(default=None, max_length=50),
    account: Account = Depends(get_current_account),
    db: Session = Depends(get_db),
) -> list[AnalysisJob]:
    return list_analysis_jobs(db, requested_by=account.id, test_id=test_id)
