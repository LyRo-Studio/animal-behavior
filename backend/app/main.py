import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.analyses import router as analyses_router
from app.api.consolidations import router as consolidations_router
from app.api.cutting_jobs import router as cutting_jobs_router
from app.api.health import router as health_router
from app.api.media_browser import public_router as media_stream_router
from app.api.media_browser import router as media_browser_router
from app.api.whoami import router as whoami_router
from app.core.config import settings
from app.core.logging import configure_access_log_redaction
from app.db.session import SessionLocal
from app.services.audit_log import prune_old_audit_events

# ADR 0002: the media-streaming endpoint's token query parameter must never
# sit in plaintext access logs — see app/core/logging.py.
configure_access_log_redaction()

logger = logging.getLogger(__name__)


def _prune_audit_log_once() -> int:
    """Opens and closes its own `Session` entirely within this one call, so
    the whole thing can run as a single `asyncio.to_thread` unit (see
    `_prune_audit_log_periodically` below) — cancelling that thread's
    *awaiting* coroutine can't actually stop the thread itself mid-query,
    so the session's lifecycle must never straddle the async/thread
    boundary: opening it here and closing it here, in the same thread,
    means an async-side cancellation never touches a `Session` a still-
    running background thread might still be using (caught in review —
    the original shape closed `db` from the event loop in a `finally`,
    racing the thread and raising SQLAlchemy's `IllegalStateChangeError`
    on shutdown).
    """
    db = SessionLocal()
    try:
        return prune_old_audit_events(db, retention_days=settings.audit_log_retention_days)
    finally:
        db.close()


async def _prune_audit_log_periodically() -> None:
    """Ticket #87, issue #79's retention policy: prunes `audit_log` rows
    older than a rolling `settings.audit_log_retention_days` window, once
    every `settings.audit_log_prune_interval_seconds` — the "lightweight
    in-process daily background task" CONTEXT.md's audit log design calls
    for, deliberately not a cron container/systemd timer/task queue (same
    single-backend-instance assumption `rate_limit.py`'s in-memory store
    already relies on). Prunes once immediately on startup and then on the
    interval, so a backend restarted shortly before the previous prune was
    due doesn't sit on an over-retention table for up to a full extra
    interval before ever pruning again.

    `prune_old_audit_events` is sync — run via `asyncio.to_thread` so a
    large delete never blocks the event loop (and therefore every
    in-flight request) for its duration. A failure here is caught and
    logged, never left to kill the loop — same "best-effort, always"
    principle CONTEXT.md's audit log design applies to every other
    audit_log write/operation.
    """
    while True:
        try:
            deleted = await asyncio.to_thread(_prune_audit_log_once)
            if deleted:
                logger.info("Pruned %d audit_log row(s) past the retention window", deleted)
        except Exception:
            logger.exception("Failed to prune audit_log")
        await asyncio.sleep(settings.audit_log_prune_interval_seconds)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # `settings.audit_log_prune_enabled` (default True) is the test-disable
    # seam for this task — see its own docstring in app/core/config.py.
    prune_task = (
        asyncio.create_task(_prune_audit_log_periodically())
        if settings.audit_log_prune_enabled
        else None
    )
    try:
        yield
    finally:
        if prune_task is not None:
            prune_task.cancel()
            # `cancel()` can't interrupt a prune already mid-DELETE inside
            # `asyncio.to_thread` — cancellation only takes effect once
            # that thread's function returns, so an unbounded `await`
            # here would otherwise block the whole ASGI shutdown for
            # however long that DELETE takes (caught in review). Bounded
            # so shutdown can't hang past a normal container stop grace
            # period; the thread itself still runs to completion on its
            # own regardless of this timeout, it just isn't waited on.
            with suppress(asyncio.CancelledError, TimeoutError):
                await asyncio.wait_for(prune_task, timeout=5)


# Everything is mounted under `/api` (ticket #71) so the reverse proxy in
# front of the app (nginx/) can send `/` to the frontend and `/api/` to this
# service with no path rewriting. The interactive docs live under it too —
# otherwise `/docs` would be routed to the frontend instead.
API_PREFIX = "/api"

app = FastAPI(
    title="Animal Behavior API",
    docs_url=f"{API_PREFIX}/docs",
    redoc_url=f"{API_PREFIX}/redoc",
    openapi_url=f"{API_PREFIX}/openapi.json",
    # A slash-redirect is an absolute URL built from the Host the backend
    # saw — `http://`, since TLS is terminated in front of it — so following
    # one from the HTTPS site would be blocked as mixed content. A stray
    # trailing slash is a plain 404 instead.
    redirect_slashes=False,
    lifespan=lifespan,
)

# No `allow_credentials`: there's no cookie/session for a cross-origin page
# to ride on (ticket #72 removed application-level authentication), so
# nothing here needs — or should be granted — credentialed cross-origin
# access.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router, prefix=API_PREFIX)
app.include_router(whoami_router, prefix=API_PREFIX)
app.include_router(media_browser_router, prefix=API_PREFIX)
app.include_router(media_stream_router, prefix=API_PREFIX)
app.include_router(analyses_router, prefix=API_PREFIX)
app.include_router(cutting_jobs_router, prefix=API_PREFIX)
app.include_router(consolidations_router, prefix=API_PREFIX)
