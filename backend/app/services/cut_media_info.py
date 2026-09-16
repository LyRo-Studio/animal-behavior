"""A Cut's probed media info (duration, resolution, codec), cached by S3 key
+ ETag (ticket #22, part of #17) — see CONTEXT.md's "Media browser — Cut
media-info caching" decision.

Distinct from `app/services/media_browser.py`'s Test/Cut catalog listing:
this module doesn't tell you what Cuts exist, only caches probed facts
about a Cut already looked up there.
"""

import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from app.models.cut_media_info import CutMediaInfo
from app.services.media_browser import CutNotFoundError, parse_cut_key
from app.services.media_prober import MediaProber
from app.services.s3_client import S3Client, S3ObjectNotFoundError


@dataclass(frozen=True)
class CutMediaInfoResult:
    duration_seconds: float
    width: int
    height: int
    codec: str


# A safe extension for the local download's filename (see `_local_filename`
# below) — a plain "." followed by up to 10 letters/digits, nothing else.
_SAFE_EXTENSION_RE = re.compile(r"^\.[A-Za-z0-9]{1,10}$")


def _local_filename(cut_filename: str) -> str:
    """A safe local filename to download `key`'s Cut to for probing —
    never `cut_filename` itself.

    `parse_cut_key`'s validation only guarantees no "/" in the filename
    component of a Cut key, not that it's a safe filesystem path segment:
    a Cut key of `cuts/T001/..` is well-formed by that check (S3 has no
    directory semantics, so ".." there is just an ordinary, if unlikely,
    object-name character sequence) but would otherwise become
    `Path(tmp_dir) / ".."` below — escaping the temporary directory
    entirely. Keeping only a validated, allowlisted extension (dropping
    the rest of the name) preserves the hint `ffprobe` may use to pick a
    demuxer while making that impossible regardless of what the S3 key
    itself contains.
    """
    extension = Path(cut_filename).suffix
    return f"cut{extension}" if _SAFE_EXTENSION_RE.match(extension) else "cut"


def get_cut_media_info(
    db: Session, s3: S3Client, prober: MediaProber, key: str
) -> CutMediaInfoResult:
    """The probed media info for the Cut at `key`.

    Raises CutNotFoundError if `key` isn't a well-formed Cut key under
    cuts/<test_id>/ (same shape-defines-existence validation as
    `parse_cut_key`'s other callers) or no object currently exists there.

    The first call for a given key+ETag probes — via a temporary local
    download (`S3Client.download_file`) through the injected `prober` — and
    caches the result; a later call for the same key+ETag reuses the cached
    row instead of re-probing. If the underlying object has since changed
    (a different ETag), this re-probes and overwrites the stale row rather
    than serving it.
    """
    filename = parse_cut_key(key)

    try:
        info = s3.head_object(key)
    except S3ObjectNotFoundError:
        raise CutNotFoundError(key) from None
    # Every S3Client.head_object implementation populates etag (it's only
    # ever unset on an S3ObjectInfo yielded by list_objects_info) — asserted
    # here to narrow `str | None` to `str` for the cache-column assignment
    # below, not defensive plumbing for a case that can actually happen.
    assert info.etag is not None

    cached = db.get(CutMediaInfo, key)
    if cached is not None and cached.etag == info.etag:
        return CutMediaInfoResult(
            duration_seconds=cached.duration_seconds,
            width=cached.width,
            height=cached.height,
            codec=cached.codec,
        )

    with tempfile.TemporaryDirectory() as tmp_dir:
        local_path = Path(tmp_dir) / _local_filename(filename)
        s3.download_file(key, local_path)
        probed = prober.probe(local_path)

    if cached is None:
        cached = CutMediaInfo(s3_key=key)
        db.add(cached)

    cached.etag = info.etag
    cached.duration_seconds = probed.duration_seconds
    cached.width = probed.width
    cached.height = probed.height
    cached.codec = probed.codec
    db.commit()

    return CutMediaInfoResult(
        duration_seconds=probed.duration_seconds,
        width=probed.width,
        height=probed.height,
        codec=probed.codec,
    )
