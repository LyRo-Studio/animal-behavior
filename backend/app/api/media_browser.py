import mimetypes
import re
from collections.abc import Iterator

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from app.api.deps import get_current_account, raise_if_throttled
from app.core.config import settings
from app.core.security import create_media_token, decode_media_token
from app.models.account import Account
from app.schemas.media_browser import (
    CutOut,
    DatasetEntryOut,
    MediaTokenAction,
    MediaTokenRequest,
    MediaTokenResponse,
)
from app.services.media_browser import (
    CutNotFoundError,
    DatasetNotFoundError,
    TestNotFoundError,
    list_cuts_for_test,
    list_dataset_folder,
    list_test_ids,
    parse_cut_key,
)
from app.services.rate_limit import RateLimiter, enforce_all, get_rate_limiter
from app.services.s3_client import S3Client, S3ObjectNotFoundError, get_s3_client

# Every authenticated Account (User or Admin) gets identical access here —
# no extra role gating (CONTEXT.md's "Media browser — access" decision).
router = APIRouter(
    prefix="/media", tags=["media-browser"], dependencies=[Depends(get_current_account)]
)

# The streaming endpoint (ticket #21) is reached by the browser's own
# native <video src> / download-link requests, which can't carry the app's
# Authorization bearer header — it's authenticated by the media token
# itself instead (see docs/adr/0002-media-access-tokens-in-url.md), so it
# lives on a separate router without `router`'s bearer-auth dependency.
public_router = APIRouter(prefix="/media", tags=["media-browser"])

# A response's body is read from S3 (and handed to the client) this many
# bytes at a time via `_iter_range`/StreamingResponse, so this endpoint
# never materializes more than one chunk of a Cut in memory at once —
# regardless of how large the requested range is, including "the whole
# object" for a plain (no-Range) download (CONTEXT.md's "backend proxies
# Cut bytes itself" decision + ENGINEERING-STANDARDS.md's DoS-protection
# guidance: "avoid unbounded... processing of unbounded input").
_STREAM_CHUNK_BYTES = 1 * 1024 * 1024

_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


@router.get("/tests", response_model=list[str])
def list_tests(s3: S3Client = Depends(get_s3_client)) -> list[str]:
    return list_test_ids(s3)


@router.get("/tests/{test_id}/cuts", response_model=list[CutOut])
def list_cuts(test_id: str, s3: S3Client = Depends(get_s3_client)) -> list[CutOut]:
    try:
        cuts = list_cuts_for_test(s3, test_id)
    except TestNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Test not found."
        ) from None
    return [CutOut.model_validate(cut) for cut in cuts]


@router.get("/datasets", response_model=list[DatasetEntryOut])
def list_datasets(path: str = "", s3: S3Client = Depends(get_s3_client)) -> list[DatasetEntryOut]:
    try:
        entries = list_dataset_folder(s3, path)
    except DatasetNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Dataset folder not found."
        ) from None
    return [DatasetEntryOut.model_validate(entry) for entry in entries]


@router.post("/cuts/token", response_model=MediaTokenResponse)
def mint_media_token(
    payload: MediaTokenRequest,
    account: Account = Depends(get_current_account),
    limiter: RateLimiter = Depends(get_rate_limiter),
) -> MediaTokenResponse:
    """Mint a short-lived, single-Cut-scoped, single-action-scoped media
    token (ticket #21) for `payload.key`/`payload.action`. Rate limited per
    Account regardless of outcome — same "hit before processing" shape as
    /auth/forgot-password, since issuance itself is the resource being
    protected (CONTEXT.md's "Media browser — abuse protection" decision).
    """
    result = limiter.hit(
        f"media-token:account:{account.id}",
        limit=settings.media_token_rate_limit_max_attempts_per_account,
        window_seconds=settings.media_token_rate_limit_window_seconds,
    )
    raise_if_throttled(enforce_all(result))

    try:
        parse_cut_key(payload.key)
    except CutNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Cut not found."
        ) from None

    token = create_media_token(cut_key=payload.key, action=payload.action.value)
    return MediaTokenResponse(token=token, expires_in=settings.media_token_expire_minutes * 60)


class _RangeUnsatisfiable(Exception):
    """A syntactically valid Range header names an out-of-bounds range
    (RFC 7233 §4.4) — the caller returns 416. Distinct from a malformed
    header (see `_resolve_range`), which is *not* this and must not 416."""


def _resolve_range(range_header: str, total_size: int) -> tuple[int, int] | None:
    """The inclusive (start, end) byte range `range_header` (an incoming
    `Range: bytes=...` header value) selects out of an object `total_size`
    bytes long, or None if there's effectively no Range request to honor —
    either the header is missing, or it's syntactically malformed, which
    RFC 7233 §2.1 says a recipient "SHOULD treat... as if it were not
    present" rather than reject outright. The caller then serves the whole
    object as 200 OK either way.

    Raises `_RangeUnsatisfiable` only for a syntactically *valid*
    range-spec whose start is at or past `total_size` (RFC 7233 §4.4) — a
    reversed range-spec (last-byte-pos < first-byte-pos) is itself
    syntactically invalid per RFC 7233 §2.1 and therefore ignored (treated
    as absent, returning None), not raised as unsatisfiable.

    Only the single-range form is supported; Cut playback/download never
    needs multipart ranges.
    """
    match = _RANGE_RE.match(range_header.strip())
    if match is None:
        return None

    start_str, end_str = match.groups()
    if start_str == "" and end_str == "":
        return None

    if start_str == "":
        # Suffix range: "bytes=-500" means the last 500 bytes.
        suffix_length = int(end_str)
        if suffix_length == 0:
            return None
        if total_size == 0:
            raise _RangeUnsatisfiable
        start = max(0, total_size - suffix_length)
        end = total_size - 1
    else:
        start = int(start_str)
        end = int(end_str) if end_str != "" else total_size - 1
        if end < start:
            # Invalid byte-range-spec (RFC 7233 §2.1) — ignore it entirely
            # rather than 416, distinct from the genuinely-out-of-bounds
            # case below.
            return None
        if start >= total_size:
            raise _RangeUnsatisfiable
        end = min(end, total_size - 1)

    return start, end


def _iter_range(s3: S3Client, key: str, start: int, end: int) -> Iterator[bytes]:
    """Yield `key`'s bytes in [start, end] (inclusive) as successive
    `_STREAM_CHUNK_BYTES`-sized reads from S3 — the actual no-full-
    buffering guarantee for the response built from this: true regardless
    of how large [start, end] is, including the entire object.
    """
    position = start
    while position <= end:
        chunk_end = min(position + _STREAM_CHUNK_BYTES - 1, end)
        yield s3.read_range(key, position, chunk_end)
        position = chunk_end + 1


def _content_disposition(disposition: str, filename: str) -> str:
    """A `Content-Disposition` header value with `filename` as a properly
    escaped quoted-string (RFC 6266 / RFC 7230 §3.2.6: a `"` or `\\` inside
    a quoted-string must be backslash-escaped) — a Cut filename is only
    guaranteed to contain no "/" (see `_CUT_KEY_RE`), not to be free of
    quote characters, and an unescaped one would otherwise break the
    header's own syntax.
    """
    escaped = filename.replace("\\", "\\\\").replace('"', '\\"')
    return f'{disposition}; filename="{escaped}"'


@public_router.get("/stream")
def stream_cut(
    key: str,
    action: MediaTokenAction,
    token: str,
    request: Request,
    s3: S3Client = Depends(get_s3_client),
) -> StreamingResponse:
    """Serve a Cut's bytes for `key`/`action`, authenticated by `token`
    alone (ticket #21) — no bearer auth, see `public_router`'s docstring.

    Forwards an incoming Range request through to S3 as a correct 206
    partial-content response, so the browser can seek during playback;
    with no (or a malformed — RFC 7233 §2.1) Range header, returns the
    whole object as 200 OK. Either way the body streams from S3 in
    `_STREAM_CHUNK_BYTES` chunks (`_iter_range`), never buffering a whole
    Cut in memory regardless of the range's size. `action=play` serves
    inline; `action=download` sets Content-Disposition so the browser
    saves it under its original filename rather than displaying it.
    """
    try:
        payload = decode_media_token(token)
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired media token."
        ) from None

    # A token minted for one Cut/action must never serve a different one —
    # this is the only place that's checked, since `key`/`action` come
    # from the request itself, independent of the token's own claims.
    if payload.get("cut_key") != key or payload.get("action") != action.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Token not valid for this Cut/action."
        )

    try:
        filename = parse_cut_key(key)
    except CutNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Cut not found."
        ) from None

    try:
        info = s3.head_object(key)
    except S3ObjectNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Cut not found."
        ) from None

    disposition = "attachment" if action is MediaTokenAction.DOWNLOAD else "inline"
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Disposition": _content_disposition(disposition, filename),
        # ADR 0002: the token travels in the URL, so responses must never
        # leak it onward via a Referer header on a subsequent request.
        "Referrer-Policy": "no-referrer",
    }

    range_header = request.headers.get("range")
    byte_range: tuple[int, int] | None = None
    if range_header is not None:
        try:
            byte_range = _resolve_range(range_header, info.size)
        except _RangeUnsatisfiable:
            headers["Content-Range"] = f"bytes */{info.size}"
            raise HTTPException(
                status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
                detail="Range not satisfiable.",
                headers=headers,
            ) from None

    if byte_range is not None:
        start, end = byte_range
        headers["Content-Range"] = f"bytes {start}-{end}/{info.size}"
        headers["Content-Length"] = str(end - start + 1)
        return StreamingResponse(
            _iter_range(s3, key, start, end),
            status_code=status.HTTP_206_PARTIAL_CONTENT,
            media_type=content_type,
            headers=headers,
        )

    headers["Content-Length"] = str(info.size)
    body = _iter_range(s3, key, 0, info.size - 1) if info.size > 0 else iter((b"",))
    return StreamingResponse(
        body, status_code=status.HTTP_200_OK, media_type=content_type, headers=headers
    )
