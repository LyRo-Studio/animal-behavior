"""Password hashing and JWT access-token handling.

Password hashing uses Argon2id (via argon2-cffi), a modern algorithm
purpose-built for password storage, per ENGINEERING-STANDARDS.md's
Password Security section. Access tokens are short-lived JWTs; refresh
tokens are opaque random strings, only ever persisted as a hash — see
docs/adr/0001-jwt-access-refresh-tokens.md.
"""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import settings

_password_hasher = PasswordHasher()

_JWT_ALGORITHM = "HS256"
_ACCESS_TOKEN_TYPE = "access"
_MEDIA_TOKEN_TYPE = "media"


def hash_password(password: str) -> str:
    """Hash a plaintext password for storage. Never log the input or output."""
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Check a plaintext password against a stored hash, without raising."""
    try:
        return _password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def create_access_token(account_id: int, role: str) -> str:
    """Issue a short-lived JWT carrying the account id and role."""
    now = datetime.now(UTC)
    payload = {
        "sub": str(account_id),
        "role": role,
        "type": _ACCESS_TOKEN_TYPE,
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=_JWT_ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    """Decode and verify an access token. Raises jwt.PyJWTError if invalid/expired."""
    payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[_JWT_ALGORITHM])
    if payload.get("type") != _ACCESS_TOKEN_TYPE:
        raise jwt.InvalidTokenError("Not an access token")
    return payload


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
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=_JWT_ALGORITHM)


def decode_media_token(token: str) -> dict[str, Any]:
    """Decode and verify a media token. Raises jwt.PyJWTError if
    invalid/expired, same contract as `decode_access_token`."""
    payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[_JWT_ALGORITHM])
    if payload.get("type") != _MEDIA_TOKEN_TYPE:
        raise jwt.InvalidTokenError("Not a media token")
    return payload


def generate_opaque_token(num_bytes: int = 48) -> str:
    """Generate a new opaque, high-entropy token.

    Shared by refresh tokens and account-action tokens (invite /
    password-reset links) — both are the same shape: a random bearer
    secret, never a user-chosen value.
    """
    return secrets.token_urlsafe(num_bytes)


def hash_opaque_token(raw_token: str) -> str:
    """Hash an opaque token for storage/lookup.

    Opaque tokens are high-entropy random strings, not user-chosen
    secrets, so a fast cryptographic hash (unlike Argon2id for passwords)
    is appropriate here — brute-forcing the hash back to the token is
    infeasible regardless of hash speed.
    """
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
