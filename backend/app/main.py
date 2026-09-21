from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.analyses import router as analyses_router
from app.api.debug import router as debug_router
from app.api.health import router as health_router
from app.api.media_browser import public_router as media_stream_router
from app.api.media_browser import router as media_browser_router
from app.api.whoami import router as whoami_router
from app.core.config import settings
from app.core.logging import configure_access_log_redaction

# ADR 0002: the media-streaming endpoint's token query parameter must never
# sit in plaintext access logs — see app/core/logging.py.
configure_access_log_redaction()

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
app.include_router(debug_router, prefix=API_PREFIX)
