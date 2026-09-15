"""S3-compatible object storage access, behind a small injectable client.

The sole S3-access seam for the media browser feature (ticket #18, part of
#17) — see CONTEXT.md's "Media browser — S3 client" and "— no Test/Cut
catalog database (for now)" decisions. Later tickets call this through the
named methods below (never a raw boto3 client of their own), so a future
catalog database can replace the live-S3 implementation without changing
callers. Tests inject `FakeS3Client` (see `tests/fakes.py`) instead of
hitting the real bucket — never mock a service function directly, same
convention as `app/services/mail.py`'s `MailTransport`.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.config import Config

from app.core.config import settings


@dataclass(frozen=True)
class S3ObjectInfo:
    key: str
    size: int
    last_modified: datetime


class S3Client(Protocol):
    def list_folders(self, prefix: str = "") -> Iterator[str]:
        """The folders directly under `prefix` (one level, not recursive),
        each as a key ending in "/"."""
        ...

    def list_objects_info(
        self, prefix: str = "", *, recursive: bool = True
    ) -> Iterator[S3ObjectInfo]:
        """Objects under `prefix`. `recursive=False` limits to objects
        directly under `prefix`, like `list_folders` does for folders."""
        ...

    def read_range(self, key: str, start: int, end: int) -> bytes:
        """The bytes of `key` in [start, end], both inclusive — the same
        bounds a `Range: bytes=start-end` HTTP header uses, so an incoming
        Range request can be forwarded without adjustment."""
        ...

    def download_file(self, key: str, local_path: Path) -> Path:
        """Download `key` to `local_path`, creating parent directories as
        needed."""
        ...


class BotoS3Client:
    """Real S3Client, backed by boto3. Works with S3-compatible storage
    services such as Ceph RGW."""

    def __init__(
        self,
        *,
        bucket_name: str,
        endpoint_url: str | None,
        access_key_id: str | None,
        secret_access_key: str | None,
        addressing_style: str,
    ) -> None:
        self._bucket_name = bucket_name
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            config=Config(
                signature_version="s3v4",
                max_pool_connections=64,
                s3={"addressing_style": addressing_style},
            ),
        )

    def list_folders(self, prefix: str = "") -> Iterator[str]:
        paginator = self._client.get_paginator("list_objects_v2")

        for page in paginator.paginate(Bucket=self._bucket_name, Prefix=prefix, Delimiter="/"):
            for common_prefix in page.get("CommonPrefixes", []):
                yield common_prefix["Prefix"]

    def list_objects_info(
        self, prefix: str = "", *, recursive: bool = True
    ) -> Iterator[S3ObjectInfo]:
        paginator = self._client.get_paginator("list_objects_v2")

        params = {"Bucket": self._bucket_name, "Prefix": prefix}
        if not recursive:
            params["Delimiter"] = "/"

        for page in paginator.paginate(**params):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if key.endswith("/"):
                    continue
                yield S3ObjectInfo(key=key, size=obj["Size"], last_modified=obj["LastModified"])

    def read_range(self, key: str, start: int, end: int) -> bytes:
        response = self._client.get_object(
            Bucket=self._bucket_name, Key=key, Range=f"bytes={start}-{end}"
        )
        return response["Body"].read()

    def download_file(self, key: str, local_path: Path) -> Path:
        local_path = Path(local_path)
        local_path.parent.mkdir(parents=True, exist_ok=True)

        # Multipart transfer tuned for large files — Cuts are video files
        # (see CONTEXT.md's "Cut" term).
        self._client.download_file(
            Bucket=self._bucket_name,
            Key=key,
            Filename=str(local_path),
            Config=TransferConfig(
                multipart_threshold=64 * 1024 * 1024,
                multipart_chunksize=64 * 1024 * 1024,
                max_concurrency=4,
                use_threads=True,
            ),
        )
        return local_path


def get_s3_client() -> S3Client:
    return BotoS3Client(
        bucket_name=settings.s3_bucket,
        endpoint_url=settings.s3_endpoint,
        access_key_id=settings.aws_access_key_id,
        secret_access_key=settings.aws_secret_access_key,
        addressing_style=settings.s3_addressing_style,
    )
