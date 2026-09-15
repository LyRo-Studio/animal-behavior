from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_current_account
from app.schemas.media_browser import CutOut
from app.services.media_browser import TestNotFoundError, list_cuts_for_test, list_test_ids
from app.services.s3_client import S3Client, get_s3_client

# Every authenticated Account (User or Admin) gets identical access here —
# no extra role gating (CONTEXT.md's "Media browser — access" decision).
router = APIRouter(
    prefix="/media", tags=["media-browser"], dependencies=[Depends(get_current_account)]
)


@router.get("/tests", response_model=list[str])
def list_tests(s3: S3Client = Depends(get_s3_client)) -> list[str]:
    return list_test_ids(s3)


@router.get("/tests/{test_id}/cuts", response_model=list[CutOut])
def list_cuts(test_id: str, s3: S3Client = Depends(get_s3_client)) -> list[CutOut]:
    try:
        cuts = list_cuts_for_test(s3, test_id)
    except TestNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Test not found."
        ) from None
    return [CutOut.model_validate(cut) for cut in cuts]
