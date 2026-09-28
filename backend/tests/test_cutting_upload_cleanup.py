"""Direct tests for the cleanup of cutting uploads that never became a job
(issue #171): `delete_abandoned_uploads`, against the real database (to see
which uploads a job points at) and a `tmp_path` upload root. Ages are set
with `os.utime`, since an upload's last activity is read from the disk."""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.models.cutting_job import CuttingJob, CuttingJobStatus
from app.services.cutting_jobs import delete_abandoned_uploads
from app.services.cutting_uploads import get_upload_status, start_upload, upload_blob_path

_NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
_SEVEN_DAYS = timedelta(days=7)


def _upload(root: Path, *, camera="C1") -> str:
    return start_upload(
        root,
        test_id="T001",
        camera=camera,
        filename=f"T001_{camera}_source.mp4",
        total_size_bytes=10,
    ).upload_id


def _last_written(root: Path, upload_id: str, when: datetime, *, only=None) -> None:
    """Backdate `upload_id`'s directory and files (or just those named in
    `only`) to `when`."""
    upload_dir = root / upload_id
    paths = [upload_dir, *upload_dir.iterdir()]
    for path in paths:
        if only is None or path.name in only:
            os.utime(path, (when.timestamp(), when.timestamp()))


def _job_pointing_at(db_session, root, upload_id, *, status, column="c1_source_path"):
    job = CuttingJob(
        test_id="T001",
        status=status,
        reference_camera="C1",
        phase_timestamps={},
        **{column: str(upload_blob_path(root, upload_id))},
    )
    db_session.add(job)
    db_session.commit()


def _cleanup(db_session, root):
    return delete_abandoned_uploads(db_session, root, abandoned_after=_SEVEN_DAYS, now=_NOW)


def test_an_upload_no_job_uses_is_deleted_once_untouched_for_too_long(db_session, tmp_path):
    upload_id = _upload(tmp_path)
    _last_written(tmp_path, upload_id, _NOW - _SEVEN_DAYS - timedelta(minutes=1))

    deleted = _cleanup(db_session, tmp_path)

    assert deleted == [upload_id]
    assert not (tmp_path / upload_id).exists()


def test_a_recently_written_upload_is_kept(db_session, tmp_path):
    upload_id = _upload(tmp_path)
    _last_written(tmp_path, upload_id, _NOW - _SEVEN_DAYS + timedelta(minutes=1))

    assert _cleanup(db_session, tmp_path) == []
    assert get_upload_status(tmp_path, upload_id).received_bytes == 0


def test_an_old_upload_that_just_received_a_chunk_is_kept(db_session, tmp_path):
    """Its age runs from the last byte written, not from when it started."""
    upload_id = _upload(tmp_path)
    _last_written(tmp_path, upload_id, _NOW - timedelta(days=30))
    _last_written(tmp_path, upload_id, _NOW - timedelta(hours=1), only={"blob"})

    assert _cleanup(db_session, tmp_path) == []
    assert (tmp_path / upload_id).exists()


@pytest.mark.parametrize(
    "status",
    [
        CuttingJobStatus.QUEUED,
        CuttingJobStatus.RUNNING,
        CuttingJobStatus.FAILED,
        CuttingJobStatus.CANCELLED,
        CuttingJobStatus.SUCCEEDED,
    ],
)
@pytest.mark.parametrize("column", ["c1_source_path", "c2_source_path"])
def test_an_old_upload_a_job_points_at_is_never_deleted(db_session, tmp_path, status, column):
    """A job's source has its own lifecycle (Discard source, #169)."""
    upload_id = _upload(tmp_path, camera="C1" if column == "c1_source_path" else "C2")
    _last_written(tmp_path, upload_id, _NOW - timedelta(days=30))
    _job_pointing_at(db_session, tmp_path, upload_id, status=status, column=column)

    assert _cleanup(db_session, tmp_path) == []
    assert (tmp_path / upload_id).exists()


def test_only_the_abandoned_uploads_among_many_are_deleted(db_session, tmp_path):
    abandoned = _upload(tmp_path)
    used = _upload(tmp_path)
    recent = _upload(tmp_path)
    for upload_id in (abandoned, used):
        _last_written(tmp_path, upload_id, _NOW - timedelta(days=30))
    _job_pointing_at(db_session, tmp_path, used, status=CuttingJobStatus.FAILED)

    assert _cleanup(db_session, tmp_path) == [abandoned]
    assert sorted(path.name for path in tmp_path.iterdir()) == sorted([used, recent])


def test_nothing_that_isnt_an_upload_is_ever_touched(db_session, tmp_path):
    stray_dir = tmp_path / "lost+found"
    stray_dir.mkdir()
    stray_file = tmp_path / "notes.txt"
    stray_file.write_text("keep me")
    for path in (stray_dir, stray_file):
        os.utime(path, ((_NOW - timedelta(days=30)).timestamp(),) * 2)

    assert _cleanup(db_session, tmp_path) == []
    assert stray_dir.exists() and stray_file.exists()


def test_a_missing_upload_root_has_nothing_to_clean_up(db_session, tmp_path):
    assert _cleanup(db_session, tmp_path / "never-created") == []


def test_an_upload_whose_delete_fails_isnt_reported_as_deleted(db_session, tmp_path, monkeypatch):
    upload_id = _upload(tmp_path)
    _last_written(tmp_path, upload_id, _NOW - timedelta(days=30))
    monkeypatch.setattr("app.services.cutting_jobs.delete_source_upload", lambda source_path: False)

    assert _cleanup(db_session, tmp_path) == []
    assert (tmp_path / upload_id).exists()
