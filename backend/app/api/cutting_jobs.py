"""Cutting-job upload intake and creation (ticket #94, part of issue #93's
Feature C) — mirrors app/api/analyses.py's "no auth, identity for
attribution only" shape (ticket #72). No cutting-worker exists yet: a
created job just sits `queued` (mirrors ticket #45's own scoping for
AnalysisJob).
"""

import logging
from dataclasses import asdict, dataclass
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import TypeAdapter, ValidationError
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
from app.models.cutting_job import CuttingJob
from app.schemas.cutting_jobs import (
    CuttingJobBatchEntries,
    CuttingJobBatchErrorOut,
    CuttingJobBatchOut,
    CuttingJobBatchResultOut,
    CuttingJobOut,
    CuttingUploadOut,
    StartCuttingUploadRequest,
)
from app.services.audit_log import record_audit_event
from app.services.cutting_jobs import (
    CUTTING_JOB_VALIDATION_ERRORS,
    CutsAlreadyExistError,
    CuttingJobNotFoundError,
    CuttingJobSubmission,
    DuplicateCameraUploadError,
    DuplicateTestInBatchError,
    IncompleteSourceUploadError,
    InvalidSourceFilenameError,
    MismatchedSourceUploadError,
    NoSourceVideoUploadedError,
    ReferenceCameraMismatchError,
    SourceVideoCollisionError,
    SourceVideoNotDecodableError,
    UnknownSourceUploadError,
    get_cutting_job,
    submit_cutting_job,
    submit_cutting_job_batch,
)
from app.services.cutting_uploads import (
    InvalidUploadFilenameError,
    UploadAlreadyCompleteError,
    UploadNotFoundError,
    UploadOffsetMismatchError,
    UploadStorageCapExceededError,
    UploadWouldExceedDeclaredSizeError,
    append_upload_chunk,
    get_cutting_upload_root,
    get_upload_status,
    start_upload,
)
from app.services.media_prober import MediaProber, get_media_prober
from app.services.rate_limit import RateLimiter, enforce_all, get_rate_limiter
from app.services.s3_client import S3Client, get_s3_client
from app.services.timestamp_excel import (
    ExcelSchemaError,
    InvalidWorkbookError,
    MalformedReferenceCameraError,
    MalformedTestIdError,
    MalformedTimestampCellError,
    TestRowNotFoundError,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cutting-jobs", tags=["cutting-jobs"])

# Ticket #96: the machine-readable marker on the "Cuts already exist" 409, so
# a client can tell it apart from the (never overridable) source-video
# collision 409 and offer a confirm-and-resubmit, without matching on the
# human-readable `detail` string.
CUTS_ALREADY_EXIST_CODE = "cuts_already_exist"


@router.post("/uploads", response_model=CuttingUploadOut, status_code=status.HTTP_201_CREATED)
def start_cutting_upload(
    payload: StartCuttingUploadRequest,
    identity: str | None = Depends(get_identity),
    limiter: RateLimiter = Depends(get_rate_limiter),
    root: Path = Depends(get_cutting_upload_root),
) -> CuttingUploadOut:
    result = limiter.hit(
        identity_rate_limit_key("cutting-upload-init", identity),
        limit=settings.cutting_upload_init_rate_limit_max_attempts_per_identity,
        window_seconds=settings.cutting_upload_init_rate_limit_window_seconds,
    )
    raise_if_throttled(enforce_all(result))

    if payload.total_size_bytes > settings.cutting_upload_max_file_size_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Declared file size is too large."
        )

    try:
        return start_upload(
            root,
            test_id=payload.test_id,
            camera=payload.camera,
            filename=payload.filename,
            total_size_bytes=payload.total_size_bytes,
        )
    except InvalidUploadFilenameError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Filename doesn't match the declared Test/camera.",
        ) from None
    except UploadStorageCapExceededError:
        raise HTTPException(
            status_code=status.HTTP_507_INSUFFICIENT_STORAGE,
            detail="Not enough retained upload storage available. Try again later.",
        ) from None


@router.get("/uploads/{upload_id}", response_model=CuttingUploadOut)
def get_cutting_upload(
    upload_id: str, root: Path = Depends(get_cutting_upload_root)
) -> CuttingUploadOut:
    try:
        return get_upload_status(root, upload_id)
    except UploadNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Upload not found."
        ) from None


@router.patch("/uploads/{upload_id}", response_model=CuttingUploadOut)
async def append_cutting_upload(
    upload_id: str,
    request: Request,
    root: Path = Depends(get_cutting_upload_root),
) -> CuttingUploadOut:
    """Append the request body to `upload_id`'s local blob, starting at the
    offset the client declares via the `Upload-Offset` header (tus-style) —
    the client learns the real current offset via `GET .../uploads/{id}` (or
    a 409's own `Upload-Offset` response header below) to resume after a
    dropped connection, rather than guessing.
    """
    offset_header = request.headers.get("Upload-Offset")
    if offset_header is None or not offset_header.isdigit():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Upload-Offset header is required."
        )

    try:
        return await append_upload_chunk(
            root, upload_id, expected_offset=int(offset_header), chunk_stream=request.stream()
        )
    except UploadNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Upload not found."
        ) from None
    except UploadAlreadyCompleteError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Upload is already complete."
        ) from None
    except UploadOffsetMismatchError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Offset mismatch — fetch the current status and resume from there.",
            headers={"Upload-Offset": str(exc.expected_offset)},
        ) from None
    except UploadWouldExceedDeclaredSizeError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded bytes exceed the declared total size.",
        ) from None


@dataclass(frozen=True)
class _Rejection:
    """How one Test's rejected submission is described to the client —
    the whole response for `POST /cutting-jobs`, one result's `error` within
    a batch."""

    status_code: int
    detail: str
    code: str | None = None


def _describe_rejection(exc: Exception) -> _Rejection:
    """`exc` is one of `CUTTING_JOB_VALIDATION_ERRORS`."""
    bad_request = status.HTTP_400_BAD_REQUEST
    match exc:
        case UnknownSourceUploadError():
            return _Rejection(bad_request, f"Unknown upload for {exc.camera}.")
        case IncompleteSourceUploadError():
            return _Rejection(bad_request, f"Upload for {exc.camera} is not complete yet.")
        case MismatchedSourceUploadError():
            return _Rejection(bad_request, f"Upload for {exc.camera} doesn't match this job.")
        case NoSourceVideoUploadedError():
            return _Rejection(bad_request, "Upload at least one source video (C1 and/or C2).")
        case DuplicateCameraUploadError():
            return _Rejection(bad_request, "Duplicate camera upload.")
        case InvalidSourceFilenameError():
            return _Rejection(
                bad_request, f"Filename doesn't match the declared Test/camera: {exc.filename}"
            )
        case (
            InvalidWorkbookError()
            | ExcelSchemaError()
            | MalformedTestIdError()
            | MalformedReferenceCameraError()
            | MalformedTimestampCellError()
        ):
            return _Rejection(bad_request, str(exc))
        case TestRowNotFoundError():
            return _Rejection(bad_request, "No row for this Test was found in the timestamp Excel.")
        case ReferenceCameraMismatchError():
            return _Rejection(
                bad_request, "The uploaded camera doesn't match this Test's reference camera."
            )
        case SourceVideoNotDecodableError():
            return _Rejection(bad_request, f"{exc.camera} video is not a decodable video file.")
        case SourceVideoCollisionError():
            return _Rejection(
                status.HTTP_409_CONFLICT,
                "A source video with this name already exists in storage.",
            )
        case CutsAlreadyExistError():
            return _Rejection(
                status.HTTP_409_CONFLICT,
                f"Cuts already exist for {exc.test_id}. Confirm to overwrite them.",
                code=CUTS_ALREADY_EXIST_CODE,
            )
    raise TypeError(f"not a cutting-job validation error: {exc!r}")


def _within_excel_size_limit(excel_bytes: bytes) -> bytes:
    """`excel_bytes`, read with a one-byte margin, unless it's over
    `cutting_job_excel_max_file_size_bytes` (ENGINEERING-STANDARDS.md §5:
    "limit request body and upload sizes") — it's parsed once per Test."""
    if len(excel_bytes) > settings.cutting_job_excel_max_file_size_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Timestamp Excel is too large."
        )
    return excel_bytes


def _record_cutting_started(db: Session, request: Request, jobs: list[CuttingJob]) -> None:
    """Write one CUTTING_STARTED audit row per newly created job (ticket
    #99), attributed with the audit log's own verified identity
    (`get_verified_identity`, like ANALYSIS_STARTED), then commit.

    Best-effort, like every audit event: `record_audit_event` already
    swallows a failed write, and the commit here is guarded too. Each job
    was already committed by `create_cutting_job`, so a failure here can
    never turn its creation into an error response.
    """
    if not jobs:
        return
    verified_identity, identity_verified = get_verified_identity(request)
    for job in jobs:
        record_audit_event(
            db,
            identity=verified_identity,
            identity_verified=identity_verified,
            action=AuditAction.CUTTING_STARTED,
            target_type="cutting_job",
            target=str(job.id),
        )
    try:
        db.commit()
    except Exception:
        logger.exception("failed to commit CUTTING_STARTED audit events")
        db.rollback()


def _enforce_create_rate_limit(limiter: RateLimiter, identity: str | None) -> None:
    """One attempt per submission, single or batch alike (CONTEXT.md's
    ticket #97 notes): a batch is one submission, however many Tests it
    names."""
    result = limiter.hit(
        identity_rate_limit_key("create-cutting-job", identity),
        limit=settings.create_cutting_job_rate_limit_max_attempts_per_identity,
        window_seconds=settings.create_cutting_job_rate_limit_window_seconds,
    )
    raise_if_throttled(enforce_all(result))


@router.post("", response_model=CuttingJobOut, status_code=status.HTTP_201_CREATED)
async def create_cutting_job_endpoint(
    request: Request,
    test_id: str = Form(..., min_length=1, max_length=50),
    excel: UploadFile = File(...),
    c1_upload_id: str | None = Form(None),
    c2_upload_id: str | None = Form(None),
    confirm_overwrite: bool = Form(False),
    identity: str | None = Depends(get_identity),
    db: Session = Depends(get_db),
    s3: S3Client = Depends(get_s3_client),
    media_prober: MediaProber = Depends(get_media_prober),
    limiter: RateLimiter = Depends(get_rate_limiter),
    root: Path = Depends(get_cutting_upload_root),
) -> CuttingJob | JSONResponse:
    """Ticket #94: rate limited per identity (reusing rate_limit.py, same
    pattern as `POST /analyses`) — an upload-triggering endpoint is at
    least as expensive a thing to trigger repeatedly (CONTEXT.md's Feature
    C decision).

    Ticket #96: re-cutting a Test that already has Cuts answers 409 with
    `code: "cuts_already_exist"` and creates nothing; resubmitting the same
    request with `confirm_overwrite=true` creates the job as normal. The
    uploads aren't consumed by the blocked request, so the follow-up reuses
    the same upload ids.
    """
    _enforce_create_rate_limit(limiter, identity)

    excel_bytes = _within_excel_size_limit(
        await excel.read(settings.cutting_job_excel_max_file_size_bytes + 1)
    )

    try:
        job = submit_cutting_job(
            db,
            submission=CuttingJobSubmission(
                test_id=test_id,
                c1_upload_id=c1_upload_id,
                c2_upload_id=c2_upload_id,
                confirm_overwrite=confirm_overwrite,
            ),
            requested_by_identity=identity,
            excel_bytes=excel_bytes,
            upload_root=root,
            s3=s3,
            media_prober=media_prober,
        )
    except CUTTING_JOB_VALIDATION_ERRORS as exc:
        rejection = _describe_rejection(exc)
        content = {"detail": rejection.detail}
        if rejection.code is not None:
            content["code"] = rejection.code
        return JSONResponse(status_code=rejection.status_code, content=content)

    _record_cutting_started(db, request, [job])
    return job


_batch_entries_adapter = TypeAdapter(CuttingJobBatchEntries)

# Generous for five entries (each at most ~200 characters) while bounding
# the raw JSON before it's parsed at all (ENGINEERING-STANDARDS.md §5).
_BATCH_TESTS_FIELD_MAX_LENGTH = 4096


@router.post("/batch", response_model=CuttingJobBatchOut)
def create_cutting_job_batch_endpoint(
    request: Request,
    tests: str = Form(..., max_length=_BATCH_TESTS_FIELD_MAX_LENGTH),
    excel: UploadFile = File(...),
    identity: str | None = Depends(get_identity),
    db: Session = Depends(get_db),
    s3: S3Client = Depends(get_s3_client),
    media_prober: MediaProber = Depends(get_media_prober),
    limiter: RateLimiter = Depends(get_rate_limiter),
    root: Path = Depends(get_cutting_upload_root),
) -> CuttingJobBatchOut:
    """Ticket #97: submit up to 5 Tests at once, each its own independent
    CuttingJob, all read from the one `excel` workbook. `tests` is a JSON
    list of `CuttingJobBatchEntryIn`.

    Always 200 once the batch itself is accepted, with one result per Test
    in submission order — a created `job`, or the `error` (status code,
    detail, and `code` where one applies) that Test alone would have got
    from `POST /cutting-jobs`. A malformed, empty or oversized `tests` list
    is a 422 (a schema-level bound, like `POST /analyses`' 1-10 Tests), and
    the same Test twice a 400 — either way, no job is created.

    A plain `def`, unlike `POST /cutting-jobs`, so up to five Tests' worth
    of ffprobe/S3/database work runs in the threadpool rather than on the
    event loop.
    """
    _enforce_create_rate_limit(limiter, identity)

    try:
        entries = _batch_entries_adapter.validate_json(tests)
    except ValidationError as exc:
        # Re-raised as FastAPI's own validation error, so the 422 has the
        # same structured `detail` list as every other one.
        raise RequestValidationError(
            [
                {**error, "loc": ("body", "tests", *error["loc"])}
                for error in exc.errors(include_url=False)
            ]
        ) from None

    excel_bytes = _within_excel_size_limit(
        excel.file.read(settings.cutting_job_excel_max_file_size_bytes + 1)
    )

    try:
        results = submit_cutting_job_batch(
            db,
            submissions=[
                CuttingJobSubmission(
                    test_id=entry.test_id,
                    c1_upload_id=entry.c1_upload_id,
                    c2_upload_id=entry.c2_upload_id,
                    confirm_overwrite=entry.confirm_overwrite,
                )
                for entry in entries
            ],
            requested_by_identity=identity,
            excel_bytes=excel_bytes,
            upload_root=root,
            s3=s3,
            media_prober=media_prober,
        )
    except DuplicateTestInBatchError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{exc.test_id} appears more than once in this batch.",
        ) from None

    _record_cutting_started(db, request, [result.job for result in results if result.job])

    return CuttingJobBatchOut(
        results=[
            CuttingJobBatchResultOut(
                test_id=result.test_id,
                job=result.job,
                error=None
                if result.error is None
                else CuttingJobBatchErrorOut(**asdict(_describe_rejection(result.error))),
            )
            for result in results
        ]
    )


@router.get("/{cutting_job_id}", response_model=CuttingJobOut)
def get_cutting_job_endpoint(cutting_job_id: int, db: Session = Depends(get_db)) -> CuttingJob:
    try:
        return get_cutting_job(db, cutting_job_id=cutting_job_id)
    except CuttingJobNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Cutting job not found."
        ) from None
