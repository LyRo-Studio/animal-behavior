from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.analyses import router as analyses_router
from app.api.health import router as health_router
from app.api.media_browser import public_router as media_stream_router
from app.api.media_browser import router as media_browser_router
from app.api.whoami import router as whoami_router
from app.core.config import settings
from app.core.logging import configure_access_log_redaction

# ADR 0002: the media-streaming endpoint's token query parameter must never
# sit in plaintext access logs — see app/core/logging.py.
configure_access_log_redaction()

app = FastAPI(title="Animal Behavior API")

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

app.include_router(health_router)
app.include_router(whoami_router)
app.include_router(media_browser_router)
app.include_router(media_stream_router)
app.include_router(analyses_router)
