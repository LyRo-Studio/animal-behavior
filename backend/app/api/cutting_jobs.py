"""Cutting-job upload intake and creation (ticket #94, part of issue #93's
Feature C) — mirrors app/api/analyses.py's "no auth, identity for
attribution only" shape (ticket #72). No cutting-worker exists yet: a
created job just sits `queued` (mirrors ticket #45's own scoping for
AnalysisJob).
"""

from dataclasses import asdict, dataclass
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import JSONResponse
from pydantic import TypeAdapter, ValidationError
from sqlalchemy.orm import Session

from app.api.deps import get_identity, identity_rate_limit_key, raise_if_throttled
from app.core.config import settings
from app.db.session import get_db
from app.models.cutting_job import CuttingJob
from app.schemas.cutting_jobs import (
    CuttingJobBatchEntryIn,
    CuttingJobBatchErrorOut,
    CuttingJobBatchOut,
    CuttingJobBatchResultOut,
    CuttingJobOut,
    CuttingUploadOut,
    StartCuttingUploadRequest,
)
from app.services.cutting_jobs import (
    CUTTING_JOB_VALIDATION_ERRORS,
    MAX_TESTS_PER_BATCH,
    CutsAlreadyExistError,
    CuttingJobNotFoundError,
    CuttingJobSubmission,
    DuplicateCameraUploadError,
    DuplicateTestInBatchError,
    EmptyBatchError,
    IncompleteSourceUploadError,
    InvalidSourceFilenameError,
    MismatchedSourceUploadError,
    NoSourceVideoUploadedError,
    ReferenceCameraMismatchError,
    SourceVideoCollisionError,
    SourceVideoNotDecodableError,
    TooManyTestsInBatchError,
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

    excel_bytes = await excel.read()

    try:
        return submit_cutting_job(
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


_BATCH_ENTRIES = TypeAdapter(list[CuttingJobBatchEntryIn])


@router.post("/batch", response_model=CuttingJobBatchOut)
def create_cutting_job_batch_endpoint(
    tests: str = Form(...),
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
    from `POST /cutting-jobs`. An empty or oversized batch, or the same Test
    twice, is a 400 with no job created.

    A plain `def`, unlike `POST /cutting-jobs`, so up to five Tests' worth
    of ffprobe/S3/database work runs in the threadpool rather than on the
    event loop.
    """
    _enforce_create_rate_limit(limiter, identity)

    try:
        entries = _BATCH_ENTRIES.validate_json(tests)
    except ValidationError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="`tests` must be a JSON list of {test_id, c1_upload_id, c2_upload_id, "
            "confirm_overwrite} entries.",
        ) from None

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
            excel_bytes=excel.file.read(),
            upload_root=root,
            s3=s3,
            media_prober=media_prober,
        )
    except EmptyBatchError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Submit at least one Test."
        ) from None
    except TooManyTestsInBatchError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Submit at most {MAX_TESTS_PER_BATCH} Tests at once.",
        ) from None
    except DuplicateTestInBatchError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{exc.test_id} appears more than once in this batch.",
        ) from None

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
