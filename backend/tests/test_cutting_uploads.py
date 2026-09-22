"""Direct tests for app/services/cutting_uploads.py (ticket #94, part of
issue #93's Feature C) — pure local-filesystem behavior, no DB/S3/ffmpeg.
`append_upload_chunk` is async (it streams a request body); driven directly
via `asyncio.run` rather than pulling in pytest-asyncio for one module.
"""

import asyncio

import pytest

from app.core.config import settings
from app.services.cutting_uploads import (
    InvalidUploadFilenameError,
    InvalidUploadSizeError,
    UploadAlreadyCompleteError,
    UploadNotFoundError,
    UploadOffsetMismatchError,
    UploadStorageCapExceededError,
    UploadWouldExceedDeclaredSizeError,
    append_upload_chunk,
    discard_upload,
    get_upload_status,
    source_filename_matches_camera,
    start_upload,
    upload_blob_path,
)


async def _chunks(*parts: bytes):
    for part in parts:
        yield part


def _append(root, upload_id, *, expected_offset, parts):
    return asyncio.run(
        append_upload_chunk(
            root, upload_id, expected_offset=expected_offset, chunk_stream=_chunks(*parts)
        )
    )


def test_source_filename_matches_camera_accepts_the_loose_assist_convention():
    assert source_filename_matches_camera("T001_C1_source.mp4", "T001", "C1")
    assert source_filename_matches_camera("t001_c2_whatever.mov", "T001", "C2")


def test_source_filename_matches_camera_rejects_wrong_camera():
    assert not source_filename_matches_camera("T001_C1_source.mp4", "T001", "C2")


def test_source_filename_matches_camera_rejects_wrong_test_id():
    assert not source_filename_matches_camera("T002_C1_source.mp4", "T001", "C1")


def test_start_upload_creates_a_zero_offset_upload(tmp_path):
    status = start_upload(
        tmp_path, test_id="T001", camera="C1", filename="T001_C1_source.mp4", total_size_bytes=10
    )

    assert status.received_bytes == 0
    assert status.complete is False
    assert status.total_size_bytes == 10


def test_start_upload_rejects_a_zero_or_negative_size(tmp_path):
    with pytest.raises(InvalidUploadSizeError):
        start_upload(
            tmp_path, test_id="T001", camera="C1", filename="T001_C1_source.mp4", total_size_bytes=0
        )
    with pytest.raises(InvalidUploadSizeError):
        start_upload(
            tmp_path,
            test_id="T001",
            camera="C1",
            filename="T001_C1_source.mp4",
            total_size_bytes=-5,
        )


def test_upload_blob_path_matches_where_bytes_are_actually_written(tmp_path):
    status = start_upload(
        tmp_path, test_id="T001", camera="C1", filename="T001_C1_source.mp4", total_size_bytes=5
    )

    _append(tmp_path, status.upload_id, expected_offset=0, parts=[b"hello"])

    assert upload_blob_path(tmp_path, status.upload_id).read_bytes() == b"hello"


def test_start_upload_rejects_a_filename_not_matching_the_declared_camera(tmp_path):
    with pytest.raises(InvalidUploadFilenameError):
        start_upload(
            tmp_path,
            test_id="T001",
            camera="C1",
            filename="T001_C2_source.mp4",
            total_size_bytes=10,
        )


def test_start_upload_rejects_over_the_storage_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "cutting_upload_storage_cap_bytes", 100)

    with pytest.raises(UploadStorageCapExceededError):
        start_upload(
            tmp_path,
            test_id="T001",
            camera="C1",
            filename="T001_C1_source.mp4",
            total_size_bytes=101,
        )


def test_start_upload_rejection_creates_nothing_on_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "cutting_upload_storage_cap_bytes", 100)

    with pytest.raises(UploadStorageCapExceededError):
        start_upload(
            tmp_path,
            test_id="T001",
            camera="C1",
            filename="T001_C1_source.mp4",
            total_size_bytes=101,
        )

    assert list(tmp_path.iterdir()) == []


def test_storage_cap_counts_existing_uploads_toward_a_new_one(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "cutting_upload_storage_cap_bytes", 100)
    first = start_upload(
        tmp_path, test_id="T001", camera="C1", filename="T001_C1_source.mp4", total_size_bytes=60
    )
    _append(tmp_path, first.upload_id, expected_offset=0, parts=[b"x" * 60])

    with pytest.raises(UploadStorageCapExceededError):
        start_upload(
            tmp_path,
            test_id="T002",
            camera="C1",
            filename="T002_C1_source.mp4",
            total_size_bytes=41,
        )


def test_get_upload_status_for_unknown_id_raises(tmp_path):
    with pytest.raises(UploadNotFoundError):
        get_upload_status(tmp_path, "does-not-exist")


def test_get_upload_status_rejects_a_path_traversal_id(tmp_path):
    """Regression (found in code review): an upload_id shaped like a path
    traversal attempt must be rejected before it's ever used to build a
    filesystem path, not just happen to fail because nothing real is there."""
    outside = tmp_path.parent / "escaped-secret.txt"
    outside.write_text("should never be reachable via an upload_id")
    try:
        with pytest.raises(UploadNotFoundError):
            get_upload_status(tmp_path, f"../{outside.name}")
    finally:
        outside.unlink()


def test_append_chunk_rejects_a_path_traversal_id(tmp_path):
    with pytest.raises(UploadNotFoundError):
        _append(tmp_path, "../../etc/passwd", expected_offset=0, parts=[b"x"])


def test_append_chunk_writes_bytes_and_advances_offset(tmp_path):
    status = start_upload(
        tmp_path, test_id="T001", camera="C1", filename="T001_C1_source.mp4", total_size_bytes=10
    )

    result = _append(tmp_path, status.upload_id, expected_offset=0, parts=[b"hello", b"world"])

    assert result.received_bytes == 10
    assert result.complete is True
    blob = tmp_path / status.upload_id / "blob"
    assert blob.read_bytes() == b"helloworld"


def test_append_chunk_supports_resuming_from_a_partial_offset(tmp_path):
    status = start_upload(
        tmp_path, test_id="T001", camera="C1", filename="T001_C1_source.mp4", total_size_bytes=10
    )
    _append(tmp_path, status.upload_id, expected_offset=0, parts=[b"hello"])

    result = _append(tmp_path, status.upload_id, expected_offset=5, parts=[b"world"])

    assert result.received_bytes == 10
    blob = tmp_path / status.upload_id / "blob"
    assert blob.read_bytes() == b"helloworld"


def test_append_chunk_rejects_a_mismatched_offset(tmp_path):
    status = start_upload(
        tmp_path, test_id="T001", camera="C1", filename="T001_C1_source.mp4", total_size_bytes=10
    )
    _append(tmp_path, status.upload_id, expected_offset=0, parts=[b"hello"])

    with pytest.raises(UploadOffsetMismatchError) as exc_info:
        _append(tmp_path, status.upload_id, expected_offset=0, parts=[b"world"])
    assert exc_info.value.expected_offset == 5

    # The mismatched attempt must not have corrupted what was already there.
    blob = tmp_path / status.upload_id / "blob"
    assert blob.read_bytes() == b"hello"


def test_append_chunk_rejects_once_already_complete(tmp_path):
    status = start_upload(
        tmp_path, test_id="T001", camera="C1", filename="T001_C1_source.mp4", total_size_bytes=5
    )
    _append(tmp_path, status.upload_id, expected_offset=0, parts=[b"hello"])

    with pytest.raises(UploadAlreadyCompleteError):
        _append(tmp_path, status.upload_id, expected_offset=5, parts=[b"!"])


def test_append_chunk_rejects_bytes_exceeding_the_declared_size(tmp_path):
    status = start_upload(
        tmp_path, test_id="T001", camera="C1", filename="T001_C1_source.mp4", total_size_bytes=5
    )

    with pytest.raises(UploadWouldExceedDeclaredSizeError):
        _append(tmp_path, status.upload_id, expected_offset=0, parts=[b"too many bytes"])


def test_append_chunk_concurrent_calls_for_the_same_upload_do_not_both_succeed(tmp_path):
    """Regression (found in code review): two concurrent PATCH calls for the
    same upload_id, both declaring expected_offset=0, must not both be
    allowed to write — exactly one succeeds; the other sees a real offset
    mismatch once the first has finished, rather than both racing past the
    offset check and corrupting the blob."""
    status = start_upload(
        tmp_path, test_id="T001", camera="C1", filename="T001_C1_source.mp4", total_size_bytes=10
    )

    async def _slow_chunks(*parts):
        for part in parts:
            await asyncio.sleep(0)  # yield to the event loop, like real I/O would
            yield part

    async def _run():
        return await asyncio.gather(
            append_upload_chunk(
                tmp_path, status.upload_id, expected_offset=0, chunk_stream=_slow_chunks(b"hello")
            ),
            append_upload_chunk(
                tmp_path, status.upload_id, expected_offset=0, chunk_stream=_slow_chunks(b"world")
            ),
            return_exceptions=True,
        )

    results = asyncio.run(_run())

    successes = [r for r in results if not isinstance(r, Exception)]
    failures = [r for r in results if isinstance(r, Exception)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], UploadOffsetMismatchError)
    blob = tmp_path / status.upload_id / "blob"
    assert blob.read_bytes() in (b"hello", b"world")


def test_append_chunk_for_unknown_upload_raises(tmp_path):
    with pytest.raises(UploadNotFoundError):
        _append(tmp_path, "does-not-exist", expected_offset=0, parts=[b"x"])


def test_discard_upload_removes_everything_on_disk(tmp_path):
    status = start_upload(
        tmp_path, test_id="T001", camera="C1", filename="T001_C1_source.mp4", total_size_bytes=5
    )

    discard_upload(tmp_path, status.upload_id)

    with pytest.raises(UploadNotFoundError):
        get_upload_status(tmp_path, status.upload_id)


def test_discard_upload_for_unknown_id_is_a_no_op(tmp_path):
    discard_upload(tmp_path, "does-not-exist")
