from fastapi import HTTPException, Request, status

from app.core.config import settings
from app.models.analysis_job import REQUESTED_BY_IDENTITY_MAX_LENGTH
from app.services.rate_limit import RateLimitResult


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
    value = request.headers.get(header_name, "").strip()
    return value[:REQUESTED_BY_IDENTITY_MAX_LENGTH] or None


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
