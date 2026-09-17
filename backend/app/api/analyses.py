from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_account
from app.db.session import get_db
from app.models.account import Account
from app.models.analysis_job import AnalysisJob
from app.schemas.analyses import AnalysisJobOut, CreateAnalysisRequest
from app.services.analyses import (
    AnalysisJobNotFoundError,
    EmptyCutSelectionError,
    InvalidCutSelectionError,
    create_analysis_job,
    get_analysis_job,
    list_analysis_jobs,
)

# Every authenticated Account (User or Admin) gets identical access here —
# no extra role gating, matching the Media Browser's existing access rule
# (CONTEXT.md's "Media browser — access" decision, carried over by issue
# #44's "Authorization" section).
router = APIRouter(
    prefix="/analyses", tags=["analyses"], dependencies=[Depends(get_current_account)]
)


@router.post("", response_model=AnalysisJobOut, status_code=status.HTTP_201_CREATED)
def create_analysis(
    payload: CreateAnalysisRequest,
    account: Account = Depends(get_current_account),
    db: Session = Depends(get_db),
) -> AnalysisJob:
    try:
        return create_analysis_job(
            db, requested_by=account.id, test_id=payload.test_id, cut_keys=payload.cuts
        )
    except EmptyCutSelectionError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Select at least one Cut to analyze."
        ) from None
    except InvalidCutSelectionError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="One or more selected Cuts are not valid C2 analysis input for this Test.",
        ) from None


@router.get("/{analysis_id}", response_model=AnalysisJobOut)
def get_analysis(
    analysis_id: int,
    account: Account = Depends(get_current_account),
    db: Session = Depends(get_db),
) -> AnalysisJob:
    try:
        return get_analysis_job(db, requested_by=account.id, analysis_id=analysis_id)
    except AnalysisJobNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Analysis not found."
        ) from None


@router.get("", response_model=list[AnalysisJobOut])
def list_analyses(
    test_id: str | None = Query(default=None, max_length=50),
    account: Account = Depends(get_current_account),
    db: Session = Depends(get_db),
) -> list[AnalysisJob]:
    return list_analysis_jobs(db, requested_by=account.id, test_id=test_id)
