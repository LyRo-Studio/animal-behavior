"""Cutting-job upload intake and creation (ticket #94, part of issue #93's
Feature C) — mirrors app/api/analyses.py's "no auth, identity for
attribution only" shape (ticket #72). No cutting-worker exists yet: a
created job just sits `queued` (mirrors ticket #45's own scoping for
AnalysisJob).
"""

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.deps import get_identity, identity_rate_limit_key, raise_if_throttled
from app.core.config import settings
from app.db.session import get_db
from app.models.cutting_job import CuttingJob
from app.schemas.cutting_jobs import CuttingJobOut, CuttingUploadOut, StartCuttingUploadRequest
from app.services.cutting_jobs import (
    CutsAlreadyExistError,
    CuttingJobNotFoundError,
    DuplicateCameraUploadError,
    InvalidSourceFilenameError,
    NoSourceVideoUploadedError,
    ReferenceCameraMismatchError,
    SourceVideoCollisionError,
    SourceVideoNotDecodableError,
    SourceVideoUpload,
    create_cutting_job,
    get_cutting_job,
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
    upload_blob_path,
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
    normalize_test_id,
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


def _resolve_upload(root: Path, upload_id: str, *, camera: str, test_id: str) -> SourceVideoUpload:
    try:
        upload_status = get_upload_status(root, upload_id)
    except UploadNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unknown upload for {camera}."
        ) from None
    if not upload_status.complete:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Upload for {camera} is not complete yet.",
        )
    if upload_status.test_id != test_id or upload_status.camera != camera:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Upload for {camera} doesn't match this job.",
        )
    return SourceVideoUpload(
        camera=camera,
        local_path=upload_blob_path(root, upload_id),
        filename=upload_status.filename,
    )


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
    result = limiter.hit(
        identity_rate_limit_key("create-cutting-job", identity),
        limit=settings.create_cutting_job_rate_limit_max_attempts_per_identity,
        window_seconds=settings.create_cutting_job_rate_limit_window_seconds,
    )
    raise_if_throttled(enforce_all(result))

    # Normalized the same way `start_upload` already normalized whatever
    # test_id an upload was declared under — otherwise a bare-numeric
    # test_id here would never match a normalized "T..." upload record.
    test_id = normalize_test_id(test_id)

    uploads = [
        _resolve_upload(root, upload_id, camera=camera, test_id=test_id)
        for camera, upload_id in (("C1", c1_upload_id), ("C2", c2_upload_id))
        if upload_id is not None
    ]

    excel_bytes = await excel.read()

    try:
        return create_cutting_job(
            db,
            requested_by_identity=identity,
            test_id=test_id,
            excel_bytes=excel_bytes,
            uploads=uploads,
            s3=s3,
            media_prober=media_prober,
            confirm_overwrite=confirm_overwrite,
        )
    except CutsAlreadyExistError as exc:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "detail": f"Cuts already exist for {exc.test_id}. Confirm to overwrite them.",
                "code": CUTS_ALREADY_EXIST_CODE,
            },
        )
    except NoSourceVideoUploadedError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Upload at least one source video (C1 and/or C2).",
        ) from None
    except DuplicateCameraUploadError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Duplicate camera upload."
        ) from None
    except InvalidSourceFilenameError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Filename doesn't match the declared Test/camera: {exc.filename}",
        ) from None
    except (
        InvalidWorkbookError,
        ExcelSchemaError,
        MalformedTestIdError,
        MalformedReferenceCameraError,
        MalformedTimestampCellError,
    ) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None
    except TestRowNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No row for this Test was found in the timestamp Excel.",
        ) from None
    except ReferenceCameraMismatchError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The uploaded camera doesn't match this Test's reference camera.",
        ) from None
    except SourceVideoNotDecodableError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{exc.camera} video is not a decodable video file.",
        ) from None
    except SourceVideoCollisionError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A source video with this name already exists in storage.",
        ) from None


@router.get("/{cutting_job_id}", response_model=CuttingJobOut)
def get_cutting_job_endpoint(cutting_job_id: int, db: Session = Depends(get_db)) -> CuttingJob:
    try:
        return get_cutting_job(db, cutting_job_id=cutting_job_id)
    except CuttingJobNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Cutting job not found."
        ) from None
