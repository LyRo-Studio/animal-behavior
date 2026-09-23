"""Excel consolidation upload/consolidate/download (ticket #115, part of
issue #113) — mirrors app/api/analyses.py's "no auth, identity for
attribution only" shape (ticket #72). Processing is synchronous: this
endpoint's response already carries the final outcome, so there is no
separate GET-by-id/status-polling endpoint here (issue #113: "simplest
reliable architecture", no worker/queue).
"""

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
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
from app.models.audit_log import AuditAction
from app.models.consolidation import Consolidation, ConsolidationCondition, ConsolidationStatus
from app.schemas.consolidations import ConsolidationOut
from app.services.audit_log import record_audit_event
from app.services.consolidation import (
    ConsolidationNotFoundError,
    ConsolidationRunner,
    create_consolidation,
    get_consolidation,
    get_consolidation_runner,
)
from app.services.rate_limit import RateLimiter, enforce_all, get_rate_limiter
from app.services.s3_client import S3Client, S3ObjectNotFoundError, get_s3_client
from app.services.s3_client import iter_object_range as _iter_range

router = APIRouter(prefix="/consolidations", tags=["consolidations"])

_ALLOWED_EXTENSION = ".xlsx"

# IANA media-types registry entry for OOXML spreadsheets — same constant
# app/api/analyses.py's report download uses.
_XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def get_consolidation_upload_root() -> Path:
    """FastAPI dependency: the scoped local temp directory an upload is
    written to for the duration of one request — see
    settings.consolidation_upload_temp_dir. Plain function, not a
    Protocol/fake pair (same reasoning as
    app.services.cutting_uploads.get_cutting_upload_root): tests just point
    it at a tmp_path, the same real filesystem code path production uses.
    """
    return Path(settings.consolidation_upload_temp_dir)


@router.post("", response_model=ConsolidationOut, status_code=status.HTTP_201_CREATED)
async def create_consolidation_endpoint(
    request: Request,
    file: UploadFile = File(...),
    condition: ConsolidationCondition = Form(...),
    identity: str | None = Depends(get_identity),
    db: Session = Depends(get_db),
    s3: S3Client = Depends(get_s3_client),
    runner: ConsolidationRunner = Depends(get_consolidation_runner),
    limiter: RateLimiter = Depends(get_rate_limiter),
    work_root: Path = Depends(get_consolidation_upload_root),
) -> Consolidation:
    """Upload one Excel file and consolidate it for `condition`, entirely
    within this request (issue #113's core flow).

    Always returns 201 with the created Consolidation, whether its outcome
    is `completed` or `failed` — a failed *consolidation attempt* is still
    a successfully handled request, not an HTTP error (issue #113 user
    story: "a failed consolidation should still appear in history with a
    clear reason"). A 4xx here means no Consolidation was created at all —
    the upload never even reached the domain code (wrong extension, empty
    file, oversized file, or throttled).
    """
    result = limiter.hit(
        identity_rate_limit_key("create-consolidation", identity),
        limit=settings.create_consolidation_rate_limit_max_attempts_per_identity,
        window_seconds=settings.create_consolidation_rate_limit_window_seconds,
    )
    raise_if_throttled(enforce_all(result))

    # Web/file validation (extension, non-empty, size) — deliberately kept
    # separate from consolidation/'s own business validation (issue #113:
    # "Separate: web/file validation from consolidation/business
    # validation where practical"). No Consolidation row is created for any
    # of these — the upload never reaches the domain code at all.
    if not file.filename or not file.filename.lower().endswith(_ALLOWED_EXTENSION):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File must be an .xlsx Excel file.",
        )

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="The uploaded file is empty."
        )
    if len(file_bytes) > settings.consolidation_max_file_size_bytes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File is too large.")

    consolidation = create_consolidation(
        db,
        requested_by_identity=identity,
        original_filename=file.filename,
        condition=condition,
        file_bytes=file_bytes,
        s3=s3,
        runner=runner,
        work_root=work_root,
    )

    verified_identity, identity_verified = get_verified_identity(request)
    record_audit_event(
        db,
        identity=verified_identity,
        identity_verified=identity_verified,
        action=(
            AuditAction.CONSOLIDATION_COMPLETED
            if consolidation.status == ConsolidationStatus.COMPLETED
            else AuditAction.CONSOLIDATION_FAILED
        ),
        target_type="consolidation",
        target=str(consolidation.id),
        failure_reason=consolidation.failure_reason,
    )
    db.commit()
    return consolidation


@router.get("/{consolidation_id}/download")
def download_consolidation(
    consolidation_id: int,
    request: Request,
    db: Session = Depends(get_db),
    s3: S3Client = Depends(get_s3_client),
) -> StreamingResponse:
    """Download `consolidation_id`'s result — a plain download, mirroring
    app.api.analyses.download_analysis_report exactly (same media type,
    same StreamingResponse + Content-Disposition shape, no media-token
    involved).
    """
    try:
        consolidation = get_consolidation(db, consolidation_id=consolidation_id)
    except ConsolidationNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Consolidation not found."
        ) from None
    if consolidation.result_storage_key is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No result is available for this consolidation.",
        )

    try:
        info = s3.head_object(consolidation.result_storage_key)
    except S3ObjectNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Result not found."
        ) from None

    verified_identity, identity_verified = get_verified_identity(request)
    record_audit_event(
        db,
        identity=verified_identity,
        identity_verified=identity_verified,
        action=AuditAction.CONSOLIDATION_DOWNLOADED,
        target_type="consolidation",
        target=str(consolidation_id),
    )
    db.commit()

    body = (
        _iter_range(s3, consolidation.result_storage_key, 0, info.size - 1)
        if info.size > 0
        else iter((b"",))
    )
    return StreamingResponse(
        body,
        media_type=_XLSX_MEDIA_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="{consolidation.original_filename}"',
            "Content-Length": str(info.size),
        },
    )
