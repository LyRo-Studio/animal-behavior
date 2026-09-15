from pathlib import Path

from app.services.s3_client import S3ObjectInfo
from tests.fakes import FakeS3Client

# FakeS3Client is the only S3Client this ticket exercises — the real,
# boto3-backed BotoS3Client is never touched by any test here (ticket #18's
# acceptance criteria: "no test in this ticket touches the real bucket").


def test_list_folders_returns_immediate_children_only():
    fake = FakeS3Client(
        objects={
            "cuts/T001/video1.mp4": b"a",
            "cuts/T001/video2.mp4": b"b",
            "cuts/T002/video1.mp4": b"c",
            "other/README.txt": b"d",
        }
    )

    folders = sorted(fake.list_folders(prefix="cuts/"))

    assert folders == ["cuts/T001/", "cuts/T002/"]


def test_list_folders_deduplicates_and_ignores_prefix_mismatches():
    fake = FakeS3Client(
        objects={
            "cuts/T001/video1.mp4": b"a",
            "cuts/T001/video2.mp4": b"b",
        }
    )

    folders = list(fake.list_folders(prefix="cuts/"))

    assert folders == ["cuts/T001/"]


def test_list_objects_info_recursive_includes_nested_objects():
    fake = FakeS3Client(
        objects={
            "cuts/T001/video1.mp4": b"12345",
            "cuts/T001/sub/video2.mp4": b"1234567",
        }
    )

    infos = {info.key: info for info in fake.list_objects_info(prefix="cuts/T001/")}

    assert set(infos) == {"cuts/T001/video1.mp4", "cuts/T001/sub/video2.mp4"}
    assert infos["cuts/T001/video1.mp4"] == S3ObjectInfo(
        key="cuts/T001/video1.mp4", size=5, last_modified=fake.last_modified
    )


def test_list_objects_info_excludes_folder_marker_keys():
    # BotoS3Client.list_objects_info skips keys ending in "/" (S3 folder
    # markers) — the fake must match, so a test asserting listing behavior
    # against the fake reflects what the real client would also return.
    fake = FakeS3Client(
        objects={
            "cuts/T001/": b"",
            "cuts/T001/video1.mp4": b"12345",
        }
    )

    keys = {info.key for info in fake.list_objects_info(prefix="cuts/T001/")}

    assert keys == {"cuts/T001/video1.mp4"}


def test_list_objects_info_non_recursive_excludes_nested_objects():
    fake = FakeS3Client(
        objects={
            "cuts/T001/video1.mp4": b"12345",
            "cuts/T001/sub/video2.mp4": b"1234567",
        }
    )

    keys = {info.key for info in fake.list_objects_info(prefix="cuts/T001/", recursive=False)}

    assert keys == {"cuts/T001/video1.mp4"}


def test_read_range_is_inclusive_of_both_ends():
    fake = FakeS3Client(objects={"cuts/T001/video1.mp4": b"0123456789"})

    assert fake.read_range("cuts/T001/video1.mp4", 2, 4) == b"234"


def test_read_range_clips_to_available_bytes():
    fake = FakeS3Client(objects={"cuts/T001/video1.mp4": b"0123456789"})

    assert fake.read_range("cuts/T001/video1.mp4", 8, 100) == b"89"


def test_download_file_writes_object_bytes_and_creates_parent_dirs(tmp_path: Path):
    fake = FakeS3Client(objects={"cuts/T001/video1.mp4": b"video-bytes"})
    destination = tmp_path / "downloads" / "T001" / "video1.mp4"

    result = fake.download_file("cuts/T001/video1.mp4", destination)

    assert result == destination
    assert destination.read_bytes() == b"video-bytes"
