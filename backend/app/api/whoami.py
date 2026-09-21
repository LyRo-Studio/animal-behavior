from fastapi import APIRouter, Depends

from app.api.deps import get_identity
from app.schemas.whoami import WhoAmIOut

router = APIRouter(tags=["whoami"])


@router.get("/whoami", response_model=WhoAmIOut)
def whoami(identity: str | None = Depends(get_identity)) -> WhoAmIOut:
    """Echo back the identity Mechatronics forwarded (ticket #72), so Home
    can show who the person is — the app has no login of its own to ask."""
    return WhoAmIOut(identity=identity)
