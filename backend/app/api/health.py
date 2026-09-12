from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def get_health() -> dict[str, str]:
    """Report that the API process is up and reachable.

    Deliberately has no dependency on the database: this endpoint is the
    walking-skeleton proof that the API itself is running, not a readiness
    probe for its dependencies.
    """
    return {"status": "ok"}
