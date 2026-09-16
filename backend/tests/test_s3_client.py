import hashlib
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

import app.services.s3_client as s3_client_module
from app.services.s3_client import BotoS3Client, S3ObjectInfo, S3ObjectNotFoundError, get_s3_client
from tests.fakes import FakeS3Client

# FakeS3Client is the only S3Client the app's own tests exercise through the
# DI seam — real bucket access is never touched (ticket #18's acceptance
# criteria: "no test in this ticket touches the real bucket"). BotoS3Client
# itself is still worth a narrow unit test of its own contract, mocking only
# the third-party boto3 client it wraps — same pattern as test_mail.py's
# SmtpMailTransport tests.


def _boto_client() -> BotoS3Client:
    return BotoS3Client(
        bucket_name="test-bucket",
        endpoint_url="https://s3.example.com",
        access_key_id="key",
        secret_access_key="secret",
        addressing_style="path",
    )


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


def test_head_object_returns_metadata_for_an_existing_key():
    fake = FakeS3Client(objects={"cuts/T001/video1.mp4": b"0123456789"})

    info = fake.head_object("cuts/T001/video1.mp4")

    assert info == S3ObjectInfo(
        key="cuts/T001/video1.mp4",
        size=10,
        last_modified=fake.last_modified,
        etag=hashlib.md5(b"0123456789").hexdigest(),
    )


def test_head_object_etag_changes_when_the_object_at_the_same_key_changes():
    """Ticket #22: a Cut's media-info cache is keyed by S3 key + ETag, so a
    replaced object at the same key must report a different ETag."""
    fake = FakeS3Client(objects={"cuts/T001/video1.mp4": b"original"})
    first_etag = fake.head_object("cuts/T001/video1.mp4").etag

    fake.objects["cuts/T001/video1.mp4"] = b"replaced"
    second_etag = fake.head_object("cuts/T001/video1.mp4").etag

    assert first_etag != second_etag


def test_head_object_raises_not_found_for_a_missing_key():
    fake = FakeS3Client(objects={})

    with pytest.raises(S3ObjectNotFoundError):
        fake.head_object("cuts/T001/missing.mp4")


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


def test_download_file_raises_not_found_for_a_missing_key(tmp_path: Path):
    fake = FakeS3Client(objects={})

    with pytest.raises(S3ObjectNotFoundError):
        fake.download_file("cuts/T001/missing.mp4", tmp_path / "video1.mp4")


# --- BotoS3Client: unit tests against a mocked boto3 client -----------------


def test_boto_list_folders_paginates_with_delimiter():
    with patch("app.services.s3_client.boto3.client") as boto_client_factory:
        mock_client = boto_client_factory.return_value
        mock_client.get_paginator.return_value.paginate.return_value = [
            {"CommonPrefixes": [{"Prefix": "cuts/T001/"}, {"Prefix": "cuts/T002/"}]}
        ]

        folders = list(_boto_client().list_folders(prefix="cuts/"))

        assert folders == ["cuts/T001/", "cuts/T002/"]
        mock_client.get_paginator.return_value.paginate.assert_called_once_with(
            Bucket="test-bucket", Prefix="cuts/", Delimiter="/"
        )


def test_boto_list_objects_info_recursive_omits_delimiter_and_filters_folder_markers():
    last_modified = datetime(2026, 1, 1, tzinfo=UTC)
    with patch("app.services.s3_client.boto3.client") as boto_client_factory:
        mock_client = boto_client_factory.return_value
        mock_client.get_paginator.return_value.paginate.return_value = [
            {
                "Contents": [
                    {"Key": "cuts/T001/", "Size": 0, "LastModified": last_modified},
                    {"Key": "cuts/T001/video1.mp4", "Size": 5, "LastModified": last_modified},
                ]
            }
        ]

        infos = list(_boto_client().list_objects_info(prefix="cuts/T001/"))

        assert infos == [
            S3ObjectInfo(key="cuts/T001/video1.mp4", size=5, last_modified=last_modified)
        ]
        _, kwargs = mock_client.get_paginator.return_value.paginate.call_args
        assert "Delimiter" not in kwargs


def test_boto_list_objects_info_non_recursive_passes_delimiter():
    with patch("app.services.s3_client.boto3.client") as boto_client_factory:
        mock_client = boto_client_factory.return_value
        mock_client.get_paginator.return_value.paginate.return_value = []

        list(_boto_client().list_objects_info(prefix="cuts/T001/", recursive=False))

        mock_client.get_paginator.return_value.paginate.assert_called_once_with(
            Bucket="test-bucket", Prefix="cuts/T001/", Delimiter="/"
        )


def test_boto_head_object_returns_metadata():
    last_modified = datetime(2026, 1, 1, tzinfo=UTC)
    with patch("app.services.s3_client.boto3.client") as boto_client_factory:
        mock_client = boto_client_factory.return_value
        mock_client.head_object.return_value = {
            "ContentLength": 12345,
            "LastModified": last_modified,
            "ETag": '"abc123"',
        }

        info = _boto_client().head_object("cuts/T001/video1.mp4")

        assert info == S3ObjectInfo(
            key="cuts/T001/video1.mp4", size=12345, last_modified=last_modified, etag="abc123"
        )
        mock_client.head_object.assert_called_once_with(
            Bucket="test-bucket", Key="cuts/T001/video1.mp4"
        )


def test_boto_head_object_raises_not_found_on_a_404_client_error():
    with patch("app.services.s3_client.boto3.client") as boto_client_factory:
        mock_client = boto_client_factory.return_value
        mock_client.head_object.side_effect = ClientError(
            {"ResponseMetadata": {"HTTPStatusCode": 404}, "Error": {"Code": "404"}},
            "HeadObject",
        )

        with pytest.raises(S3ObjectNotFoundError):
            _boto_client().head_object("cuts/T001/missing.mp4")


def test_boto_head_object_reraises_a_non_404_client_error():
    with patch("app.services.s3_client.boto3.client") as boto_client_factory:
        mock_client = boto_client_factory.return_value
        mock_client.head_object.side_effect = ClientError(
            {"ResponseMetadata": {"HTTPStatusCode": 403}, "Error": {"Code": "AccessDenied"}},
            "HeadObject",
        )

        with pytest.raises(ClientError):
            _boto_client().head_object("cuts/T001/video1.mp4")


def test_boto_read_range_uses_inclusive_byte_range_header():
    with patch("app.services.s3_client.boto3.client") as boto_client_factory:
        mock_client = boto_client_factory.return_value
        mock_client.get_object.return_value = {"Body": MagicMock(read=lambda: b"chunk")}

        result = _boto_client().read_range("cuts/T001/video1.mp4", 10, 20)

        assert result == b"chunk"
        mock_client.get_object.assert_called_once_with(
            Bucket="test-bucket", Key="cuts/T001/video1.mp4", Range="bytes=10-20"
        )


def test_boto_download_file_downloads_to_temp_path_then_replaces_atomically(tmp_path: Path):
    destination = tmp_path / "T001" / "video1.mp4"

    with patch("app.services.s3_client.boto3.client") as boto_client_factory:
        mock_client = boto_client_factory.return_value

        def fake_download(*, Bucket, Key, Filename, Config):
            # Simulate boto3 writing the transfer to the given temp path —
            # the real destination must not exist until the rename.
            Path(Filename).write_bytes(b"video-bytes")
            assert not destination.exists()

        mock_client.download_file.side_effect = fake_download

        result = _boto_client().download_file("cuts/T001/video1.mp4", destination)

        assert result == destination
        assert destination.read_bytes() == b"video-bytes"
        assert not destination.with_name(destination.name + ".part").exists()


def test_boto_download_file_leaves_no_partial_file_when_transfer_fails(tmp_path: Path):
    destination = tmp_path / "T001" / "video1.mp4"

    with patch("app.services.s3_client.boto3.client") as boto_client_factory:
        mock_client = boto_client_factory.return_value

        def failing_download(*, Bucket, Key, Filename, Config):
            Path(Filename).write_bytes(b"partial")
            raise RuntimeError("simulated transfer failure")

        mock_client.download_file.side_effect = failing_download

        with pytest.raises(RuntimeError):
            _boto_client().download_file("cuts/T001/video1.mp4", destination)

        assert not destination.exists()


def test_boto_download_file_raises_not_found_when_the_object_is_gone_by_download_time(
    tmp_path: Path,
):
    """Ticket #22: the object can vanish (deleted/replaced) in the gap
    between an earlier head_object check and this download — that must
    surface as the same S3ObjectNotFoundError a missing key always does,
    not an unhandled ClientError."""
    destination = tmp_path / "T001" / "video1.mp4"

    with patch("app.services.s3_client.boto3.client") as boto_client_factory:
        mock_client = boto_client_factory.return_value
        mock_client.download_file.side_effect = ClientError(
            {"ResponseMetadata": {"HTTPStatusCode": 404}, "Error": {"Code": "404"}},
            "GetObject",
        )

        with pytest.raises(S3ObjectNotFoundError):
            _boto_client().download_file("cuts/T001/video1.mp4", destination)

        assert not destination.exists()


# --- get_s3_client: fail-closed configuration and singleton caching --------


@patch.object(s3_client_module, "_s3_client", None)
def test_get_s3_client_raises_when_not_configured(monkeypatch):
    monkeypatch.setattr(s3_client_module.settings, "s3_bucket", None)
    monkeypatch.setattr(s3_client_module.settings, "s3_endpoint", None)
    monkeypatch.setattr(s3_client_module.settings, "aws_access_key_id", None)
    monkeypatch.setattr(s3_client_module.settings, "aws_secret_access_key", None)

    with pytest.raises(RuntimeError):
        get_s3_client()


@patch.object(s3_client_module, "_s3_client", None)
def test_get_s3_client_returns_the_same_instance_across_calls(monkeypatch):
    monkeypatch.setattr(s3_client_module.settings, "s3_bucket", "test-bucket")
    monkeypatch.setattr(s3_client_module.settings, "s3_endpoint", "https://s3.example.com")
    monkeypatch.setattr(s3_client_module.settings, "aws_access_key_id", "key")
    monkeypatch.setattr(s3_client_module.settings, "aws_secret_access_key", "secret")

    with patch("app.services.s3_client.boto3.client"):
        first = get_s3_client()
        second = get_s3_client()

    assert first is second
