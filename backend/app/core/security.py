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


def generate_refresh_token() -> str:
    """Generate a new opaque, high-entropy refresh token."""
    return secrets.token_urlsafe(48)


def hash_refresh_token(raw_token: str) -> str:
    """Hash an opaque refresh token for storage/lookup.

    Refresh tokens are high-entropy random strings, not user-chosen
    secrets, so a fast cryptographic hash (unlike Argon2id for passwords)
    is appropriate here — brute-forcing the hash back to the token is
    infeasible regardless of hash speed.
    """
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
