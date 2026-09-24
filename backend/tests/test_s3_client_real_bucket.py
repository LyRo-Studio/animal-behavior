"""Real-bucket smoke test for the S3-access layer (ticket #23, part of #17).

Exercises `BotoS3Client` against the actual S3-compatible bucket and its
real gateway — the one thing the `FakeS3Client`-backed tests in
test_s3_client.py (ticket #18's "no test in this ticket touches the real
bucket" decision) can never catch: gateway-level behavior that diverges
from the S3 API it otherwise mimics. This is exactly how presigned URLs
being rejected (CONTEXT.md's "Media browser — no presigned S3 URLs"
decision) was originally discovered.

Opt-in only, via the `real_bucket` marker — pyproject.toml's default
`addopts` excludes it, so a plain `pytest` run and CI never touch the
network or need real credentials. Run explicitly with:

    pytest -m real_bucket tests/test_s3_client_real_bucket.py

Skips (rather than fails) when S3 isn't configured, so an accidental
`-m real_bucket` run in an environment without real credentials gets a
clear skip reason instead of a confusing connection/auth error.
"""

import pytest

from app.core.config import settings
from app.services.s3_client import BotoS3Client

pytestmark = pytest.mark.real_bucket


def _real_client() -> BotoS3Client:
    if not (
        settings.s3_bucket
        and settings.s3_endpoint
        and settings.aws_access_key_id
        and settings.aws_secret_access_key
    ):
        pytest.skip(
            "S3 not configured — set S3_BUCKET/S3_ENDPOINT/AWS_ACCESS_KEY_ID/"
            "AWS_SECRET_ACCESS_KEY (see .env.example) to run the real-bucket smoke test."
        )
    return BotoS3Client(
        bucket_name=settings.s3_bucket,
        endpoint_url=settings.s3_endpoint,
        access_key_id=settings.aws_access_key_id.get_secret_value(),
        secret_access_key=settings.aws_secret_access_key.get_secret_value(),
        addressing_style=settings.s3_addressing_style,
    )


def _any_real_object_key(client: BotoS3Client, *, min_size: int = 0) -> str:
    """An arbitrary real object to exercise head/read against — deliberately
    not a hardcoded bucket-specific key, since the bucket's actual contents
    aren't this test's concern and may change over time.

    `min_size` excludes zero-byte objects for range-read: `bytes=0--1` (from
    a size-0 `min(16, size)` chunk) is itself an invalid Range header, which
    would either surface as an opaque ClientError or, if the gateway ignores
    it and returns the empty body anyway, pass without exercising Range
    semantics at all — defeating this file's whole point.
    """
    for info in client.list_objects_info():
        if info.size > min_size:
            return info.key
    pytest.fail("Real bucket has no matching object to test head/range-read against.")


def test_list_folders_reaches_the_real_gateway():
    client = _real_client()

    folders = list(client.list_folders())

    assert folders, "Expected at least one top-level folder in the real bucket."
    assert all(folder.endswith("/") for folder in folders)


def test_head_object_reaches_the_real_gateway():
    client = _real_client()
    key = _any_real_object_key(client)

    info = client.head_object(key)

    assert info.key == key
    assert info.size > 0
    assert info.etag


def test_range_read_reaches_the_real_gateway_and_honors_the_range():
    """Reads two distinct byte ranges and checks both the lengths and the
    content differ — not just that *a* response came back. A gateway that
    silently ignored the Range header (rather than honoring HTTP partial
    content, ticket #21's whole reason for proxying playback/download
    itself) would otherwise pass a length-only check by coincidence."""
    client = _real_client()
    key = _any_real_object_key(client, min_size=1)
    size = client.head_object(key).size

    chunk_size = min(16, size)
    start_chunk = client.read_range(key, 0, chunk_size - 1)
    assert len(start_chunk) == chunk_size

    if size > chunk_size * 2:
        middle = size // 2
        middle_chunk = client.read_range(key, middle, middle + chunk_size - 1)
        assert len(middle_chunk) == chunk_size
        assert middle_chunk != start_chunk
