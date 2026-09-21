"""Signed media tokens for Cut streaming/download.

The only thing the backend signs since ticket #72 removed application-level
authentication (docs/adr/0004-trust-mega-tronics-remove-application-auth.md):
no passwords, no login/refresh tokens. See
docs/adr/0002-media-access-tokens-in-url.md for why these exist at all.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from app.core.config import settings

_JWT_ALGORITHM = "HS256"
_MEDIA_TOKEN_TYPE = "media"


def create_media_token(cut_key: str, action: str) -> str:
    """Issue a short-lived, single-Cut-scoped, single-action-scoped media
    token (ticket #21 — see docs/adr/0002-media-access-tokens-in-url.md).

    Deliberately stateless (a signed JWT, not a DB row): the token itself
    carries the Cut key and action it's scoped to, so the streaming
    endpoint verifies a request against those claims directly rather than
    a lookup — no revocation is needed within its short lifetime (see
    CONTEXT.md's "Media browser — no access audit trail (for now)"
    decision).
    """
    now = datetime.now(UTC)
    payload = {
        "cut_key": cut_key,
        "action": action,
        "type": _MEDIA_TOKEN_TYPE,
        "iat": now,
        "exp": now + timedelta(minutes=settings.media_token_expire_minutes),
    }
    return jwt.encode(payload, settings.media_token_secret_key, algorithm=_JWT_ALGORITHM)


def decode_media_token(token: str) -> dict[str, Any]:
    """Decode and verify a media token. Raises jwt.PyJWTError if invalid/expired."""
    payload = jwt.decode(token, settings.media_token_secret_key, algorithms=[_JWT_ALGORITHM])
    if payload.get("type") != _MEDIA_TOKEN_TYPE:
        raise jwt.InvalidTokenError("Not a media token")
    return payload
