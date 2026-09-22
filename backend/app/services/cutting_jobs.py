"""Cutting-job request validation and persistence (ticket #94, part of
issue #93's Feature C). No cutting-worker yet — `create_cutting_job` only
ever creates a `queued` job and its `cutting_job_outputs` rows, mirroring
ticket #45's own scoping for AnalysisJob ("data model and request/status
API before the worker exists").
"""

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from app.models.cutting_job import CuttingJob, CuttingJobOutput, CuttingJobOutputStatus
from app.services.cutting_uploads import source_filename_matches_camera
from app.services.media_prober import MediaProbeError, MediaProber
from app.services.s3_client import S3Client, S3ObjectNotFoundError
from app.services.timestamp_excel import find_test_row, normalize_test_id, parse_timestamp_workbook

# One (condition, phase) slot per expected CuttingJobOutput, per uploaded
# camera — ZE_F8 deliberately excluded: every phase is sliced [this
# phase's start, next phase's start), so the last phase (ZE_F8, the 16th
# and final start-only timestamp) has no timestamp to bound its own end and
# can never itself become a Cut (CONTEXT.md's Feature C "ZE_F8 cannot be
# produced" decision, confirmed structurally absent across every `assist`
# branch). Excel's ZE_F8 cell is still parsed (needed to bound ZE_F7's end,
# once a cutting step exists) — it's just never in this list.
_EXPECTED_OUTPUT_SLOTS = [("ME", f"F{n}") for n in range(1, 9)] + [
    ("ZE", f"F{n}") for n in range(1, 8)
]


@dataclass(frozen=True)
class SourceVideoUpload:
    """One uploaded source video, already resolved to a local file — the
    API layer resolves an upload_id (app/services/cutting_uploads.py) into
    this; direct tests of this module construct it from a plain local file,
    with no upload-tracking machinery involved (issue #93's Test seams).
    """

    camera: str
    local_path: Path
    filename: str


class NoSourceVideoUploadedError(Exception):
    """`uploads` is empty — a job needs at least one camera's source video."""


class DuplicateCameraUploadError(Exception):
    """More than one upload claims the same camera."""

    def __init__(self, camera: str) -> None:
        self.camera = camera
        super().__init__(camera)


class InvalidSourceFilenameError(Exception):
    """An uploaded video's filename doesn't loosely match `assist`'s own
    Test/camera convention (re-checked here independent of whatever the
    upload-intake endpoint already validated — same defensive-revalidation
    principle as app/services/analyses.py's `_validate_cut_key`)."""

    def __init__(self, filename: str) -> None:
        self.filename = filename
        super().__init__(filename)


class ReferenceCameraMismatchError(Exception):
    """Exactly one camera was uploaded, and it isn't the Excel row's
    reference camera (CONTEXT.md's Feature C decision: `assist` only
    computes a real audio-sync offset when both cameras are present; with
    only the non-reference camera it would silently default to a zero
    offset instead)."""

    def __init__(self, uploaded_camera: str, reference_camera: str) -> None:
        self.uploaded_camera = uploaded_camera
        self.reference_camera = reference_camera
        super().__init__(uploaded_camera, reference_camera)


class SourceVideoNotDecodableError(Exception):
    """An uploaded video failed ffprobe validation — not a decodable video
    (CONTEXT.md's Feature C decision)."""

    def __init__(self, camera: str) -> None:
        self.camera = camera
        super().__init__(camera)


class SourceVideoCollisionError(Exception):
    """An uploaded source video's derived key already matches an existing
    S3 object (CONTEXT.md's Feature C "S3 collisions" decision: rejected
    outright at upload time, never a silent overwrite)."""

    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(key)


class CuttingJobNotFoundError(Exception):
    """Raised for a nonexistent CuttingJob id."""


def _derive_source_collision_key(test_id: str, filename: str) -> str:
    """The S3-style key an uploaded source video's own filename is checked
    for a collision against.

    Implementation judgment call (CONTEXT.md's Feature C spec names the
    *policy* — "an uploaded source video whose derived filename already
    matches an existing S3 object is rejected outright" — but not the exact
    derivation): reuses the already-validated upload filename verbatim
    under a `source/<test_id>/` namespace, mirroring `cuts/<test_id>/`'s
    existing convention (app/services/media_browser.py's `CUTS_PREFIX`).
    Revisit if a real collision report shows this doesn't match what
    legacy `source/` objects are actually named.
    """
    return f"source/{test_id}/{filename}"


def create_cutting_job(
    db: Session,
    *,
    requested_by_identity: str | None,
    test_id: str,
    excel_bytes: bytes,
    uploads: list[SourceVideoUpload],
    s3: S3Client,
    media_prober: MediaProber,
) -> CuttingJob:
    """Create a `queued` CuttingJob for `test_id`, attributed to
    `requested_by_identity`, only once every ingestion validation rule from
    issue #93 passes — no partial job is ever created if any check fails.

    `uploads` holds 1-2 already-uploaded source videos (one per camera).
    The Excel's matching row for `test_id` supplies the reference camera
    and phase timestamps; a single-camera upload is only accepted if it
    matches that reference camera. Every uploaded video is probed with
    `media_prober` (rejecting anything not decodable) and checked against
    `s3` for a derived-filename collision before the job (and its expected
    `CuttingJobOutput` rows) is created.
    """
    if not uploads:
        raise NoSourceVideoUploadedError

    # Normalized up front (same as the Excel row's own Test ID cell) so a
    # bare-numeric `test_id` (e.g. "513") matches a well-formed "T513_C1_..."
    # upload filename below, instead of being falsely rejected against the
    # un-normalized form — a real gap found in review.
    test_id = normalize_test_id(test_id)

    seen_cameras: set[str] = set()
    for upload in uploads:
        if upload.camera in seen_cameras:
            raise DuplicateCameraUploadError(upload.camera)
        seen_cameras.add(upload.camera)
        if not source_filename_matches_camera(upload.filename, test_id, upload.camera):
            raise InvalidSourceFilenameError(upload.filename)

    rows = parse_timestamp_workbook(excel_bytes)
    row = find_test_row(rows, test_id)

    if len(uploads) == 1 and uploads[0].camera != row.reference_camera:
        raise ReferenceCameraMismatchError(uploads[0].camera, row.reference_camera)

    for upload in uploads:
        try:
            media_prober.probe(upload.local_path)
        except MediaProbeError:
            raise SourceVideoNotDecodableError(upload.camera) from None

    for upload in uploads:
        key = _derive_source_collision_key(row.test_id, upload.filename)
        try:
            s3.head_object(key)
        except S3ObjectNotFoundError:
            pass
        else:
            raise SourceVideoCollisionError(key)

    job = CuttingJob(
        test_id=row.test_id,
        requested_by_identity=requested_by_identity,
        reference_camera=row.reference_camera,
        phase_timestamps=row.phase_timestamps,
        c1_source_path=next(
            (str(upload.local_path) for upload in uploads if upload.camera == "C1"), None
        ),
        c2_source_path=next(
            (str(upload.local_path) for upload in uploads if upload.camera == "C2"), None
        ),
    )
    job.outputs = [
        CuttingJobOutput(
            camera=upload.camera,
            condition=condition,
            phase=phase,
            status=CuttingJobOutputStatus.PENDING,
        )
        for upload in uploads
        for condition, phase in _EXPECTED_OUTPUT_SLOTS
        if f"{condition}_{phase}" in row.phase_timestamps
    ]

    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def get_cutting_job(db: Session, *, cutting_job_id: int) -> CuttingJob:
    job = db.get(CuttingJob, cutting_job_id)
    if job is None:
        raise CuttingJobNotFoundError(cutting_job_id)
    return job
