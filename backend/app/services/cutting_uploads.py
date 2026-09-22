"""Chunked/resumable local upload intake for CuttingJob source videos
(ticket #94, part of issue #93's Feature C).

Each upload is tracked entirely on the local filesystem under its own
directory (`meta.json` for the declared shape, `blob` for the bytes
received so far) — `blob`'s own size on disk *is* the received-byte count,
so a dropped connection and a later resumed PATCH (or a backend restart)
never need a separate, independently-maintained counter that could drift
from what's actually on disk. Source videos never reach S3, not even
transiently (CONTEXT.md's Feature C "Upload mechanics" decision) — this
module is the entire storage layer for them.
"""

import asyncio
import json
import re
import shutil
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

from app.core.config import settings
from app.services.timestamp_excel import normalize_test_id

_META_FILENAME = "meta.json"
_BLOB_FILENAME = "blob"

# Every real upload_id is minted by `start_upload` as `uuid.uuid4().hex` —
# 32 lowercase hex characters, never a path separator or "..". Anything not
# matching this shape is rejected before it ever reaches a filesystem path
# (`_upload_dir`), the same "shape-defines-existence" convention
# app/services/media_browser.py's `_TEST_ID_RE`/`_CUT_KEY_RE` already use —
# a caller-supplied id containing "../" must never be able to walk a
# constructed path outside `root` (found in review: `_upload_dir` had no
# validation of its own before this).
_UPLOAD_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def source_filename_matches_camera(filename: str, test_id: str, camera: str) -> bool:
    """Whether `filename` loosely looks like a source video for `test_id`'s
    `camera` — `assist`'s own rule, reused so this app's own filename
    validation can't drift from what the tool that actually consumes the
    file already expects.

    `test_id` must already be normalized (`normalize_test_id`) — callers
    that skip that step would falsely reject a well-formed
    "T513_C1_..." filename against a still-bare-numeric "513" test_id
    (a real gap found in review: a client naming the Test the same way its
    own Excel cell does, bare-numeric, would otherwise never match its own
    correctly-named upload).
    """
    upper = filename.upper()
    return upper.startswith(test_id.upper()) and f"_{camera.upper()}_" in upper


class UploadNotFoundError(Exception):
    """No upload exists with the given id."""

    def __init__(self, upload_id: str) -> None:
        self.upload_id = upload_id
        super().__init__(upload_id)


class UploadOffsetMismatchError(Exception):
    """The client's declared offset doesn't match the upload's actual
    current size — the caller must fetch the current status
    (`get_upload_status`) and resync before retrying, rather than silently
    duplicating or skipping bytes (this is what makes resuming after a
    dropped connection safe)."""

    def __init__(self, expected_offset: int) -> None:
        self.expected_offset = expected_offset
        super().__init__(expected_offset)


class UploadAlreadyCompleteError(Exception):
    """`upload_id` already received every byte of its declared
    `total_size_bytes` — nothing left to append."""


class UploadWouldExceedDeclaredSizeError(Exception):
    """Appending the given chunk would push the upload past its own
    declared `total_size_bytes`."""


class InvalidUploadFilenameError(Exception):
    """`filename` doesn't loosely match `assist`'s own Test/camera
    convention (see `source_filename_matches_camera`)."""

    def __init__(self, filename: str) -> None:
        self.filename = filename
        super().__init__(filename)


class UploadStorageCapExceededError(Exception):
    """Starting this upload would push total retained temp-upload storage
    over the configured cap (CONTEXT.md's Feature C decision: "bounded by
    a disk-usage threshold on total retained temp storage... rejects new
    uploads once crossed")."""


class InvalidUploadSizeError(Exception):
    """`total_size_bytes` isn't a positive integer — re-checked here
    independent of the API schema's own `gt=0` constraint, same defensive-
    revalidation principle as app/services/analyses.py's re-checked limits,
    for a direct caller that bypasses the schema layer entirely."""


@dataclass(frozen=True)
class UploadStatus:
    upload_id: str
    test_id: str
    camera: str
    filename: str
    total_size_bytes: int
    received_bytes: int

    @property
    def complete(self) -> bool:
        return self.received_bytes >= self.total_size_bytes


def get_cutting_upload_root() -> Path:
    """FastAPI dependency: the scoped local temp directory uploads land in
    — see `settings.cutting_upload_temp_dir`. A plain function (not a
    Protocol/fake pair like `S3Client`/`MediaProber`) since tests just point
    it at a `tmp_path`, the same real filesystem code path production uses."""
    return Path(settings.cutting_upload_temp_dir)


def _upload_dir(root: Path, upload_id: str) -> Path:
    """`root`'s subdirectory for `upload_id`. Raises UploadNotFoundError if
    `upload_id` doesn't match the shape every real id has (see
    `_UPLOAD_ID_RE`) — checked before building a path from it at all, so an
    id containing e.g. "../" can never resolve outside `root`."""
    if not _UPLOAD_ID_RE.match(upload_id):
        raise UploadNotFoundError(upload_id)
    return root / upload_id


def upload_blob_path(root: Path, upload_id: str) -> Path:
    """The local path of `upload_id`'s received bytes — what a completed
    upload's `SourceVideoUpload.local_path` (app/services/cutting_jobs.py)
    points at, once the API layer resolves an upload_id into one."""
    return _upload_dir(root, upload_id) / _BLOB_FILENAME


def _current_usage_bytes(root: Path) -> int:
    """Total bytes currently retained across every upload under `root` —
    what the storage cap (CONTEXT.md's Feature C decision) is actually
    checked against, not the box's total free disk space directly."""
    if not root.exists():
        return 0
    total = 0
    for entry in root.iterdir():
        blob = entry / _BLOB_FILENAME
        if blob.is_file():
            total += blob.stat().st_size
    return total


def start_upload(
    root: Path, *, test_id: str, camera: str, filename: str, total_size_bytes: int
) -> UploadStatus:
    """Begin tracking a new chunked upload, allocating its local directory
    up front (empty `blob`, offset 0) — appended to via
    `append_upload_chunk`.

    `test_id` is normalized (`normalize_test_id`) up front, same as the
    Excel row's own Test ID cell — the stored/returned `test_id` (and thus
    what a later `create_cutting_job` call compares its own normalized
    `test_id` against) is always the canonical `T`-prefixed form, regardless
    of which form the caller declared.

    Raises InvalidUploadSizeError if `total_size_bytes` isn't positive,
    InvalidUploadFilenameError if `filename` doesn't loosely match
    `assist`'s own Test/camera convention, and
    UploadStorageCapExceededError if accepting a file of this declared size
    would push total retained temp-upload storage over the configured cap
    — checked (and rejected) before anything is created on disk.
    """
    test_id = normalize_test_id(test_id)
    if total_size_bytes <= 0:
        raise InvalidUploadSizeError(total_size_bytes)
    if not source_filename_matches_camera(filename, test_id, camera):
        raise InvalidUploadFilenameError(filename)

    root.mkdir(parents=True, exist_ok=True)
    if _current_usage_bytes(root) + total_size_bytes > settings.cutting_upload_storage_cap_bytes:
        raise UploadStorageCapExceededError

    upload_id = uuid.uuid4().hex
    upload_dir = _upload_dir(root, upload_id)
    upload_dir.mkdir()
    (upload_dir / _BLOB_FILENAME).touch()
    (upload_dir / _META_FILENAME).write_text(
        json.dumps(
            {
                "test_id": test_id,
                "camera": camera,
                "filename": filename,
                "total_size_bytes": total_size_bytes,
            }
        )
    )
    return UploadStatus(
        upload_id=upload_id,
        test_id=test_id,
        camera=camera,
        filename=filename,
        total_size_bytes=total_size_bytes,
        received_bytes=0,
    )


# One asyncio.Lock per in-flight upload, serializing concurrent PATCH calls
# for the same upload_id — found in review: without this, two concurrent
# chunk-appends for the same upload could both read the same
# `received_bytes` offset (via `get_upload_status`) before either writes,
# then both append to the same `blob`, interleaving or duplicating bytes
# into a silently corrupted file that still ends up reporting `complete`.
# Single-process, in-memory, same "acceptable at this app's scale"
# reasoning as rate_limit.py's own in-memory state (one backend instance —
# docker-compose.yml runs no replicas). Entries are removed once an upload
# completes or is discarded (see below) so this doesn't grow unboundedly
# over a long-running process's lifetime.
_upload_locks: dict[str, asyncio.Lock] = {}


def _lock_for(upload_id: str) -> asyncio.Lock:
    lock = _upload_locks.get(upload_id)
    if lock is None:
        lock = asyncio.Lock()
        _upload_locks[upload_id] = lock
    return lock


def get_upload_status(root: Path, upload_id: str) -> UploadStatus:
    upload_dir = _upload_dir(root, upload_id)
    meta_path = upload_dir / _META_FILENAME
    blob_path = upload_dir / _BLOB_FILENAME
    if not meta_path.is_file() or not blob_path.is_file():
        raise UploadNotFoundError(upload_id)

    meta = json.loads(meta_path.read_text())
    return UploadStatus(
        upload_id=upload_id,
        test_id=meta["test_id"],
        camera=meta["camera"],
        filename=meta["filename"],
        total_size_bytes=meta["total_size_bytes"],
        received_bytes=blob_path.stat().st_size,
    )


async def append_upload_chunk(
    root: Path, upload_id: str, *, expected_offset: int, chunk_stream: AsyncIterator[bytes]
) -> UploadStatus:
    """Append `chunk_stream` (e.g. `Request.stream()`) to `upload_id`'s
    blob, starting at `expected_offset` — one `write()` per network read,
    never buffering the whole chunk/request body in memory first
    (ENGINEERING-STANDARDS.md §5's DoS guidance).

    Raises UploadOffsetMismatchError if `expected_offset` doesn't match the
    upload's actual current size (see that error's docstring),
    UploadAlreadyCompleteError if the upload already has every declared
    byte, and UploadWouldExceedDeclaredSizeError if the incoming bytes
    would push it past `total_size_bytes` — in the last case, whatever was
    already written to disk before the offending chunk stays (the blob's
    own size remains the single source of truth for a later status check
    or resume attempt).

    Serialized per `upload_id` (see `_lock_for`) — a second concurrent call
    for the same upload waits for the first to finish rather than racing it.
    """
    if not _UPLOAD_ID_RE.match(upload_id):
        raise UploadNotFoundError(upload_id)

    async with _lock_for(upload_id):
        status = get_upload_status(root, upload_id)
        if status.complete:
            raise UploadAlreadyCompleteError
        if expected_offset != status.received_bytes:
            raise UploadOffsetMismatchError(status.received_bytes)

        blob_path = _upload_dir(root, upload_id) / _BLOB_FILENAME
        written = 0
        with blob_path.open("ab") as f:
            async for chunk in chunk_stream:
                if status.received_bytes + written + len(chunk) > status.total_size_bytes:
                    raise UploadWouldExceedDeclaredSizeError
                f.write(chunk)
                written += len(chunk)

        result = get_upload_status(root, upload_id)

    if result.complete:
        _upload_locks.pop(upload_id, None)
    return result


def discard_upload(root: Path, upload_id: str) -> None:
    """Delete `upload_id`'s local temp storage entirely. A no-op if it's
    already gone, or if `upload_id` isn't even shaped like a real id.
    Called once a CuttingJob has consumed the upload (no caller does this
    yet in this ticket — there's no cutting-worker to discard a succeeded
    job's source, and a failed job is meant to retain it until explicitly
    retried or cancelled, neither of which this ticket builds); exposed now
    so that future step has the seam ready.
    """
    try:
        upload_dir = _upload_dir(root, upload_id)
    except UploadNotFoundError:
        return
    shutil.rmtree(upload_dir, ignore_errors=True)
    _upload_locks.pop(upload_id, None)
