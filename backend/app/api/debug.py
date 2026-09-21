from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.deps import get_identity
from app.core.config import settings
from app.schemas.debug import RequestHeadersOut
from app.services import request_diagnostics


def require_enabled() -> None:
    """404 — as if the route didn't exist — unless the diagnostic has been
    switched on with `DEBUG_REQUEST_HEADERS_ENABLED`."""
    if not settings.debug_request_headers_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")


# Not in the OpenAPI schema: it is a temporary, normally-absent diagnostic.
router = APIRouter(
    prefix="/debug",
    tags=["debug"],
    dependencies=[Depends(require_enabled)],
    include_in_schema=False,
)


@router.get("/request-headers", response_model=RequestHeadersOut)
def request_headers(
    request: Request, identity: str | None = Depends(get_identity)
) -> RequestHeadersOut:
    """Ticket #72's identity-header discovery — see
    app/services/request_diagnostics.py for why it is safe(ish) to expose
    and when it should be deleted.

    From the browser console on the hosted site:
        fetch('/api/debug/request-headers').then(r => r.json()).then(console.log)
    """
    return RequestHeadersOut(
        identity_header_name=settings.identity_header_name or None,
        identity_header_present=identity is not None,
        headers=request_diagnostics.describe_headers(request.headers),
        recent_media_requests=request_diagnostics.recent_media_requests(),
    )
