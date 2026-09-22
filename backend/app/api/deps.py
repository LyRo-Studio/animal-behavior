import logging
from functools import lru_cache

import jwt
from fastapi import HTTPException, Request, status
from jwt import PyJWKClient

from app.core.config import settings
from app.models.analysis_job import REQUESTED_BY_IDENTITY_MAX_LENGTH
from app.services.rate_limit import RateLimitResult

logger = logging.getLogger(__name__)


def _bounded_identity(raw: str) -> str | None:
    """Trim and cut an untrusted identity-header value down to
    `REQUESTED_BY_IDENTITY_MAX_LENGTH` (ENGINEERING-STANDARDS.md §5), or
    None if nothing's left. Shared by `get_identity` and
    `get_verified_identity` below so the two trust paths can't silently
    diverge on this one convention."""
    return raw.strip()[:REQUESTED_BY_IDENTITY_MAX_LENGTH] or None


def get_identity(request: Request) -> str | None:
    """The identity Mechatronics forwarded for this request, or None.

    Ticket #72 / docs/adr/0004-trust-mega-tronics-remove-application-auth.md:
    the app no longer authenticates anyone — Mechatronics does — so this is
    used **only for attribution** (which analysis was run by whom, and as a
    rate-limit key), never to allow or deny anything.

    None when `identity_header_name` isn't configured, the header is absent
    (local development has no Mechatronics in front of it), or it's blank.
    The value is untrusted input (ENGINEERING-STANDARDS.md §5) headed for a
    bounded DB column, so it's trimmed and cut to `REQUESTED_BY_IDENTITY_MAX_LENGTH`
    rather than rejected: an over-long value must never turn an otherwise
    valid request into a 500 from the database, or a 400 that would lock
    someone out of the whole app over a header they don't control.

    There is deliberately no verification that the header really came from
    Mechatronics: anything that can reach the backend directly can claim any
    identity — the network topology is what makes that safe (ADR-0004).
    """
    header_name = settings.identity_header_name
    if not header_name:
        return None
    return _bounded_identity(request.headers.get(header_name, ""))


# Fixed header names, deliberately not driven by `settings.identity_header_name`
# (which is configurable, and used by `get_identity` above for ordinary,
# unverified attribution). The audit log's stronger trust model (ADR-0005)
# is scoped to exactly these three authentik-forwarded headers — widening
# it to whatever header happens to be configured for `get_identity` would
# blur that boundary.
_AUTHENTIK_EMAIL_HEADER = "X-authentik-email"
_AUTHENTIK_JWT_HEADER = "X-authentik-jwt"
_AUTHENTIK_JWKS_HEADER = "X-authentik-meta-jwks"

# The claim authentik's forwarded JWT is expected to carry the person's
# email under (standard OIDC claim name) — checked against `X-authentik-email`
# below so "verified" means "this JWT itself asserts this identity," not
# merely "some validly-signed JWT was present." Unconfirmed against a real
# token from `dogtrace-app` (same caveat as CONTEXT.md's identity-header
# discovery); revisit if authentik turns out to use a different claim name.
_JWT_EMAIL_CLAIM = "email"

_JWT_VERIFY_ALGORITHMS = ["RS256"]


@lru_cache(maxsize=8)
def _jwk_client_for(jwks_url: str) -> PyJWKClient:
    """One `PyJWKClient` per JWKS URL, reused across calls so its own
    in-process signing-key cache (ADR-0005: "PyJWKClient's default
    in-process caching is relied on as-is") actually persists between
    requests instead of being rebuilt, and refetched from scratch, on
    every single call."""
    return PyJWKClient(jwks_url)


def get_verified_identity(request: Request) -> tuple[str | None, bool]:
    """The identity Mechatronics forwarded, and whether it was
    cryptographically verified — ticket #82 / issue #79's audit log, and
    docs/adr/0005-jwt-verified-identity-for-audit-log-only.md.

    A deliberately *stronger* trust model than `get_identity` above,
    scoped only to audit-log call sites: verifies `X-authentik-jwt`
    against the JWKS at `X-authentik-meta-jwks` (both also forwarded by
    Mechatronics) via `PyJWKClient`, *and* that the verified token's own
    `email` claim matches `X-authentik-email` — a validly-signed JWT for
    a *different* identity than the header being logged must not be
    accepted as proof of the header's identity. Returns `(None, False)`
    when `X-authentik-email` is absent or blank (e.g. local development,
    with no Mechatronics in front). Otherwise always returns that header's
    value (trimmed and bounded, same convention as `get_identity`) — with
    `identity_verified=True` only when the JWT verifies and its claim
    matches, and `False` (never raising) for a missing/expired/bad-signature
    JWT, an unreachable JWKS endpoint, or an email-claim mismatch. ADR-0005:
    verification failure must never block the action this identity would
    be attributed to.
    """
    email = _bounded_identity(request.headers.get(_AUTHENTIK_EMAIL_HEADER, ""))
    if email is None:
        return None, False

    token = request.headers.get(_AUTHENTIK_JWT_HEADER)
    jwks_url = request.headers.get(_AUTHENTIK_JWKS_HEADER)
    if not token or not jwks_url:
        return email, False

    try:
        signing_key = _jwk_client_for(jwks_url).get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=_JWT_VERIFY_ALGORITHMS,
            options={"verify_aud": False},
        )
    except Exception:
        logger.warning("Audit-log identity verification failed", exc_info=True)
        return email, False

    if payload.get(_JWT_EMAIL_CLAIM) != email:
        logger.warning("Audit-log JWT email claim does not match X-authentik-email")
        return email, False

    return email, True


def identity_rate_limit_key(prefix: str, identity: str | None) -> str:
    """The rate-limit bucket for `prefix` (e.g. "cut-info") and the caller.

    With no identity (no header configured or sent) every caller shares one
    "anonymous" bucket rather than skipping the check: a rate limit that
    silently disappears whenever the header is missing would leave the
    expensive operations it guards (ENGINEERING-STANDARDS.md §5, DoS)
    unprotected exactly when something is misconfigured. In practice this
    only ever applies to local development.
    """
    if identity is None:
        return f"{prefix}:anonymous"
    return f"{prefix}:identity:{identity}"


def raise_if_throttled(result: RateLimitResult | None) -> None:
    """Raise 429 for a rate_limit.enforce_all(...) result that found
    something exceeded; a no-op when nothing was (result is None). An HTTP
    concern (the raised status code), so this lives here rather than in
    app/services/rate_limit.py itself.
    """
    if result is None:
        return
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Too many attempts. Please try again later.",
        headers={"Retry-After": str(int(result.retry_after_seconds) + 1)},
    )
