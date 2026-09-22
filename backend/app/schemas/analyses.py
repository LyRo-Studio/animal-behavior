from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from app.models.analysis_job import AnalysisJobStatus, AnalysisJobVideoStatus

# Same bound as media_browser's own MediaTokenRequest.key — generous enough
# for any real Cut key, never persisted/hashed, just feeding straight into
# format validation (see app/services/analyses.py).
_CutKey = Annotated[str, Field(min_length=1, max_length=1024)]

# Same bound as AnalysisJobTest.test_id (String(50)).
_TestId = Annotated[str, Field(min_length=1, max_length=50)]


class CreateAnalysisRequest(BaseModel):
    """Ticket #89 / issue #88's Feature B: a job spans 1-10 Tests. `cuts`
    omitted (or None) means wholesale — every listed Test's C2-eligible Cuts
    are derived server-side (app/services/analyses.py). Hand-picking `cuts`
    is only valid when exactly one Test is listed — today's exact request,
    unchanged in meaning; given alongside more than one `test_id` it's
    rejected (400, not a schema-level 422 — see
    CutsWithMultipleTestsError) since that's a business rule, not a shape
    one.
    """

    # 1-10 Tests per job (CONTEXT.md's Feature B limit).
    test_ids: list[_TestId] = Field(min_length=1, max_length=10)
    # Bounded per ENGINEERING-STANDARDS.md's DoS-protection guidance — a
    # real Test has on the order of 30 Cuts (CONTEXT.md), so 200 is
    # generous headroom without leaving the list genuinely unbounded.
    cuts: list[_CutKey] | None = Field(default=None, min_length=1, max_length=200)


class AnalysisJobVideoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    cut_key: str
    position: int
    status: AnalysisJobVideoStatus
    failure_reason: str | None


class AnalysisJobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    # Every Test this job touches, in submission order (ticket #89 —
    # replaces the old single `test_id` string).
    test_ids: list[str]
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
