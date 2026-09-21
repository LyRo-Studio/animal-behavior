"""Temporary support for ticket #72's identity-header discovery.

The real name of the header Mechatronics forwards the person's identity in is
unknown until someone looks at what actually reaches the backend on
`dogtrace-app`, and the media-token gate (docs/adr/0002-media-access-tokens-in-url.md)
needs to know whether a *native* browser request — `<video src>`, not a JS
`fetch()` — carries that header too. Both answers come from `GET
/api/debug/request-headers` (app/api/debug.py), which is off unless
`DEBUG_REQUEST_HEADERS_ENABLED` is set and is meant to be deleted once
discovery is done.

An endpoint that echoes request headers must not double as a way to read a
credential back out, so:

- values of credential-bearing headers are replaced, never returned — judged
  by the header's name *and* by what the value looks like (a JWT or a
  `Bearer ...` value under an innocuous name is caught too), and a query
  string is cut from any value (URL-carrying headers such as `Referer` would
  otherwise echo the media token);
- every returned value is length-capped, and a repeated header shows every
  value rather than only the last;
- the record kept for `/media/stream` requests holds header *names* only —
  no values, and no query string, which is where the media token lives.
"""

import re
from collections import deque
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from app.core.config import settings

REDACTED = "[redacted]"
MAX_VALUE_LENGTH = 200
MAX_RECENT_MEDIA_REQUESTS = 20

# Never returned, whatever they contain.
_ALWAYS_REDACTED = frozenset({"cookie", "set-cookie", "authorization", "proxy-authorization"})
# Anything else that sounds like a credential. Deliberately does NOT include
# "auth" or "user": the identity header being looked for is quite possibly
# called something like `X-Auth-User`, and hiding it would defeat the point.
_REDACTED_NAME_FRAGMENTS = (
    "token",
    "secret",
    "password",
    "credential",
    "key",
    "session",
    "jwt",
    "bearer",
    "assertion",
    "signature",
)
# By value, whatever the header is called. A JWT always starts `ey` (base64 of
# `{"`); requiring that keeps a dotted, email-style identity from matching.
_JWT_RE = re.compile(r"^ey[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*$")
_AUTH_SCHEME_RE = re.compile(r"^(bearer|basic|digest|negotiate)\s+\S", re.IGNORECASE)

_recent_media_requests: deque[dict[str, Any]] = deque(maxlen=MAX_RECENT_MEDIA_REQUESTS)


def is_sensitive_header(name: str) -> bool:
    lowered = name.lower()
    return lowered in _ALWAYS_REDACTED or any(f in lowered for f in _REDACTED_NAME_FRAGMENTS)


def _describe_value(name: str, value: str) -> str:
    if is_sensitive_header(name) or _JWT_RE.match(value) or _AUTH_SCHEME_RE.match(value):
        return REDACTED
    if "?" in value:
        # A query string is where the media token (or any other secret a URL
        # carries) would be; the path in front of it is what is useful.
        value = value.split("?", 1)[0] + "?[query stripped]"
    if len(value) > MAX_VALUE_LENGTH:
        value = value[:MAX_VALUE_LENGTH] + "…"
    return value


def describe_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """`headers` as a name -> value dict that is safe to hand back to the
    person who sent them. A header sent more than once lists every value,
    comma-separated (as HTTP itself folds them): collapsing to the last would
    hide an appended — or spoofed — identity."""
    values: dict[str, list[str]] = {}
    for name, value in headers.items():
        name = name.lower()
        values.setdefault(name, []).append(_describe_value(name, value))
    return {name: ", ".join(parts) for name, parts in values.items()}


def record_media_request(headers: Mapping[str, str], *, identity_present: bool) -> None:
    """Remember that a `/media/stream` request arrived, and which header
    *names* it carried. A no-op unless the diagnostic is enabled.

    `identity_present` comes from the caller (`get_identity`) rather than
    being re-derived here, so this can't drift from how the app itself reads
    the identity."""
    if not settings.debug_request_headers_enabled:
        return
    _recent_media_requests.append(
        {
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
            # What kind of element made the request (`video`, `empty` for
            # fetch, `document` for navigation, ...) — a browser-set value.
            "sec_fetch_dest": (headers.get("sec-fetch-dest") or "")[:50] or None,
            "identity_header_present": identity_present,
            "header_names": sorted(name.lower() for name in headers.keys()),
        }
    )


def recent_media_requests() -> list[dict[str, Any]]:
    return list(_recent_media_requests)


def clear_recent_media_requests() -> None:
    _recent_media_requests.clear()
