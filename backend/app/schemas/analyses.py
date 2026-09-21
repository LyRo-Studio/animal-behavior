from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from app.models.analysis_job import AnalysisJobStatus, AnalysisJobVideoStatus

# Same bound as media_browser's own MediaTokenRequest.key — generous enough
# for any real Cut key, never persisted/hashed, just feeding straight into
# format validation (see app/services/analyses.py).
_CutKey = Annotated[str, Field(min_length=1, max_length=1024)]


class CreateAnalysisRequest(BaseModel):
    test_id: str = Field(min_length=1, max_length=50)
    # Bounded per ENGINEERING-STANDARDS.md's DoS-protection guidance — a
    # real Test has on the order of 30 Cuts (CONTEXT.md), so 200 is
    # generous headroom without leaving the list genuinely unbounded.
    cuts: list[_CutKey] = Field(min_length=1, max_length=200)


class AnalysisJobVideoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    cut_key: str
    position: int
    status: AnalysisJobVideoStatus
    failure_reason: str | None


class AnalysisJobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    test_id: str
    # Who ran it, as forwarded by Mechatronics (ticket #72) — null if no
    # identity header was present at the time.
    requested_by_identity: str | None
    status: AnalysisJobStatus
    dogtrace_version: str | None
    report_available: bool
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    videos: list[AnalysisJobVideoOut]
