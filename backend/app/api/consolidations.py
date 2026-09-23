"""Excel consolidation upload/consolidate/download (ticket #115, part of
issue #113), its history list (ticket #116) and rename (ticket #117) —
mirrors app/api/analyses.py's "no auth, identity for attribution only"
shape (ticket #72). Processing is synchronous: the create endpoint's
response already carries the final outcome, so there is no separate
GET-by-id/status-polling endpoint here (issue #113: "simplest reliable
architecture", no worker/queue).
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
from app.api.media_browser import content_disposition
from app.core.config import settings
from app.db.session import get_db
from app.models.audit_log import AuditAction
from app.models.consolidation import (
    ORIGINAL_FILENAME_MAX_LENGTH,
    Consolidation,
    ConsolidationCondition,
    ConsolidationStatus,
)
from app.schemas.consolidations import ConsolidationOut, RenameConsolidationRequest
from app.services.audit_log import record_audit_event
from app.services.consolidation import (
    ConsolidationNotFoundError,
    ConsolidationRunner,
    InvalidWorkbookError,
    get_consolidation,
    get_consolidation_runner,
    list_consolidations,
    rename_consolidation,
    run_consolidation,
    start_consolidation,
    validate_workbook_opens,
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


def _record_verified_audit_event(
    db: Session,
    request: Request,
    *,
    action: AuditAction,
    consolidation_id: int,
    failure_reason: str | None = None,
) -> None:
    """Same shape as app.api.analyses._record_verified_audit_event — logged
    with the audit log's own verified identity, and always commits
    afterward, so no call site can forget to (`record_audit_event` never
    commits itself)."""
    verified_identity, identity_verified = get_verified_identity(request)
    record_audit_event(
        db,
        identity=verified_identity,
        identity_verified=identity_verified,
        action=action,
        target_type="consolidation",
        target=str(consolidation_id),
        failure_reason=failure_reason,
    )
    db.commit()


# A plain `def`, not `async def`: FastAPI then runs it in its threadpool, so
# the blocking workbook check and the synchronous consolidation itself
# never stall the event loop for every other request (caught in review).
@router.post("", response_model=ConsolidationOut, status_code=status.HTTP_201_CREATED)
def create_consolidation_endpoint(
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
    the upload never even reached the domain code (wrong extension, empty,
    oversized or unopenable file, or throttled).
    """
    result = limiter.hit(
        identity_rate_limit_key("create-consolidation", identity),
        limit=settings.create_consolidation_rate_limit_max_attempts_per_identity,
        window_seconds=settings.create_consolidation_rate_limit_window_seconds,
    )
    raise_if_throttled(enforce_all(result))

    # Web/file validation (extension, non-empty, size, opens as a workbook)
    # — deliberately kept separate from consolidation/'s own business
    # validation (issue #113: "Separate: web/file validation from
    # consolidation/business validation where practical"). No Consolidation
    # row is created for any of these — the upload never reaches the domain
    # code at all.
    if not file.filename or not file.filename.lower().endswith(_ALLOWED_EXTENSION):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File must be an .xlsx Excel file.",
        )
    if len(file.filename) > ORIGINAL_FILENAME_MAX_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Filename must be at most {ORIGINAL_FILENAME_MAX_LENGTH} characters.",
        )

    file_bytes = file.file.read()
    if not file_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="The uploaded file is empty."
        )
    if len(file_bytes) > settings.consolidation_max_file_size_bytes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File is too large.")

    try:
        validate_workbook_opens(file_bytes)
    except InvalidWorkbookError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None

    consolidation = start_consolidation(
        db,
        requested_by_identity=identity,
        original_filename=file.filename,
        condition=condition,
        input_size_bytes=len(file_bytes),
    )
    _record_verified_audit_event(
        db, request, action=AuditAction.CONSOLIDATION_STARTED, consolidation_id=consolidation.id
    )

    consolidation = run_consolidation(
        db,
        consolidation,
        file_bytes=file_bytes,
        s3=s3,
        runner=runner,
        work_root=work_root,
    )

    _record_verified_audit_event(
        db,
        request,
        action=(
            AuditAction.CONSOLIDATION_COMPLETED
            if consolidation.status == ConsolidationStatus.COMPLETED
            else AuditAction.CONSOLIDATION_FAILED
        ),
        consolidation_id=consolidation.id,
        failure_reason=consolidation.failure_reason,
    )
    return consolidation


@router.get("", response_model=list[ConsolidationOut])
def list_consolidations_endpoint(db: Session = Depends(get_db)) -> list[Consolidation]:
    """Every Consolidation, newest first, whoever requested it — no
    per-identity filtering (ticket #116, mirroring app.api.analyses'
    list_analyses). Includes `failed` rows with their failure_reason."""
    return list_consolidations(db)


@router.patch("/{consolidation_id}", response_model=ConsolidationOut)
def rename_consolidation_endpoint(
    consolidation_id: int,
    payload: RenameConsolidationRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> Consolidation:
    """Set or clear `consolidation_id`'s display_name (ticket #117). No
    ownership check — fully shared, like download (ADR-0004). No CSRF token,
    like every other state-changing endpoint since ticket #72 — see
    CONTEXT.md's "Consolidation rename" decision for why, and for the
    app-wide open question that leaves."""
    try:
        consolidation = rename_consolidation(
            db, consolidation_id=consolidation_id, display_name=payload.display_name
        )
    except ConsolidationNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Consolidation not found."
        ) from None

    _record_verified_audit_event(
        db, request, action=AuditAction.CONSOLIDATION_RENAMED, consolidation_id=consolidation_id
    )
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

    _record_verified_audit_event(
        db, request, action=AuditAction.CONSOLIDATION_DOWNLOADED, consolidation_id=consolidation_id
    )

    body = (
        _iter_range(s3, consolidation.result_storage_key, 0, info.size - 1)
        if info.size > 0
        else iter((b"",))
    )
    return StreamingResponse(
        body,
        media_type=_XLSX_MEDIA_TYPE,
        headers={
            "Content-Disposition": content_disposition(
                "attachment", consolidation.original_filename
            ),
            "Content-Length": str(info.size),
        },
    )
