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
from botocore.exceptions import ClientError

from app.core.config import settings


@dataclass(frozen=True)
class S3ObjectInfo:
    key: str
    size: int
    last_modified: datetime
    # The object's current S3 ETag (unquoted). Ticket #22: `head_object`'s
    # etag is the cache key (alongside the S3 key itself) for a Cut's
    # probed media info — a replaced object at the same key gets a new
    # ETag, which is what tells that cache to re-probe rather than serve a
    # stale result. Not populated by `list_objects_info` — nothing today
    # needs a listed object's ETag, only a single head-object lookup's.
    etag: str | None = None


class S3ObjectNotFoundError(Exception):
    """No object exists at the given key — raised by `head_object`, the
    sole existence check in this Protocol (see its docstring)."""


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

    def head_object(self, key: str) -> S3ObjectInfo:
        """Metadata (key, size, last-modified) for a single object at
        `key`, without downloading it — used to size a streaming response
        (ticket #21) before any bytes are read. Raises S3ObjectNotFoundError
        if no such object exists."""
        ...

    def read_range(self, key: str, start: int, end: int) -> bytes:
        """The bytes of `key` in [start, end], both inclusive — the same
        bounds a `Range: bytes=start-end` HTTP header uses, so an incoming
        Range request can be forwarded without adjustment."""
        ...

    def download_file(self, key: str, local_path: Path) -> Path:
        """Download `key` to `local_path`, creating parent directories as
        needed. Raises S3ObjectNotFoundError if no such object exists —
        including if it existed when an earlier `head_object` call checked
        but was deleted before this call ran."""
        ...

    def upload_file(self, local_path: Path, key: str) -> None:
        """Upload the local file at `local_path` to `key`, creating it (or
        overwriting whatever already exists there) — the analysis worker's
        report-persistence step (ticket #47, part of #44), the only current
        caller."""
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

    def head_object(self, key: str) -> S3ObjectInfo:
        try:
            response = self._client.head_object(Bucket=self._bucket_name, Key=key)
        except ClientError as exc:
            # HeadObject has no response body, so S3 reports a missing key
            # as a bare "404" HTTP status rather than a modeled error code
            # like GetObject's "NoSuchKey" — check the status directly.
            status_code = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if status_code == 404:
                raise S3ObjectNotFoundError(key) from None
            raise
        return S3ObjectInfo(
            key=key,
            size=response["ContentLength"],
            last_modified=response["LastModified"],
            etag=response["ETag"].strip('"'),
        )

    def read_range(self, key: str, start: int, end: int) -> bytes:
        response = self._client.get_object(
            Bucket=self._bucket_name, Key=key, Range=f"bytes={start}-{end}"
        )
        return response["Body"].read()

    def download_file(self, key: str, local_path: Path) -> Path:
        local_path = Path(local_path)
        local_path.parent.mkdir(parents=True, exist_ok=True)

        # Download to a `.part` sibling and atomically replace the
        # destination only on success — a failure partway through a
        # multipart transfer must never leave a truncated file at
        # `local_path` for a later probe or retry to mistake for the real
        # Cut. Matches the previous root-level utility's behavior.
        temp_path = local_path.with_name(local_path.name + ".part")

        # Multipart transfer tuned for large files — Cuts are video files
        # (see CONTEXT.md's "Cut" term).
        try:
            self._client.download_file(
                Bucket=self._bucket_name,
                Key=key,
                Filename=str(temp_path),
                Config=TransferConfig(
                    multipart_threshold=64 * 1024 * 1024,
                    multipart_chunksize=64 * 1024 * 1024,
                    max_concurrency=4,
                    use_threads=True,
                ),
            )
        except ClientError as exc:
            # Same "no modeled error code" situation as head_object's 404
            # handling above — and the case this actually needs to catch:
            # the object existed when an earlier head_object call checked,
            # then was deleted/replaced before this download ran.
            status_code = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if status_code == 404:
                temp_path.unlink(missing_ok=True)
                raise S3ObjectNotFoundError(key) from None
            raise
        temp_path.replace(local_path)
        return local_path

    def upload_file(self, local_path: Path, key: str) -> None:
        # Same multipart tuning as download_file — report artifacts can
        # include the rendered/annotated videos DogTrace produces, not just
        # small report files.
        self._client.upload_file(
            Filename=str(local_path),
            Bucket=self._bucket_name,
            Key=key,
            Config=TransferConfig(
                multipart_threshold=64 * 1024 * 1024,
                multipart_chunksize=64 * 1024 * 1024,
                max_concurrency=4,
                use_threads=True,
            ),
        )


_s3_client: S3Client | None = None


def get_s3_client() -> S3Client:
    """The process-lifetime S3 client, built once and reused across
    requests. A boto3 client owns a real connection pool, so — unlike
    `get_mail_transport`'s fresh `SmtpMailTransport` per call — constructing
    one per request would churn connections on every listing/range request,
    including repeated video seeks. Safe to share: nothing about a Cut's
    read path is per-request state.

    Fails closed: unlike SMTP's "log instead of send", there's no safe
    fallback for object storage, so an unconfigured bucket/endpoint/
    credentials raises here rather than silently falling through to
    boto3's ambient (process/instance) credential chain against whatever
    account and bucket that chain happens to resolve.
    """
    global _s3_client
    if _s3_client is None:
        if not (
            settings.s3_bucket
            and settings.s3_endpoint
            and settings.aws_access_key_id
            and settings.aws_secret_access_key
        ):
            raise RuntimeError(
                "S3 is not configured — set S3_BUCKET, S3_ENDPOINT, "
                "AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY (see .env.example)."
            )
        _s3_client = BotoS3Client(
            bucket_name=settings.s3_bucket,
            endpoint_url=settings.s3_endpoint,
            access_key_id=settings.aws_access_key_id,
            secret_access_key=settings.aws_secret_access_key,
            addressing_style=settings.s3_addressing_style,
        )
    return _s3_client
