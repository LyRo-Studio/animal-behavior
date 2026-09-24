from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from app.models.cutting_job import CuttingJobOutputStatus, CuttingJobStatus
from app.services.cutting_jobs import MAX_TESTS_PER_BATCH

_TestId = Annotated[str, Field(min_length=1, max_length=50)]
_Filename = Annotated[str, Field(min_length=1, max_length=255)]


class StartCuttingUploadRequest(BaseModel):
    """Ticket #94: begin a chunked/resumable upload for one Test's C1 or C2
    source video — see app/services/cutting_uploads.py."""

    test_id: _TestId
    camera: str = Field(pattern="^(C1|C2)$")
    filename: _Filename
    total_size_bytes: int = Field(gt=0)


class CuttingUploadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    upload_id: str
    test_id: str
    camera: str
    filename: str
    total_size_bytes: int
    received_bytes: int
    complete: bool


class CuttingJobOutputOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    camera: str
    condition: str
    phase: str
    status: CuttingJobOutputStatus
    failure_reason: str | None


class CuttingJobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    test_id: str
    requested_by_identity: str | None
    status: CuttingJobStatus
    reference_camera: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    outputs: list[CuttingJobOutputOut]


class CuttingJobBatchEntryIn(BaseModel):
    """Ticket #97: one Test's entry in `POST /cutting-jobs/batch`'s `tests`
    list — the same fields `POST /cutting-jobs` takes for a single Test."""

    model_config = ConfigDict(extra="forbid")

    test_id: _TestId
    c1_upload_id: str | None = None
    c2_upload_id: str | None = None
    confirm_overwrite: bool = False


# `POST /cutting-jobs/batch`'s whole `tests` list: 1-5 entries, bounded at
# the schema level like CreateAnalysisRequest.test_ids (the service enforces
# the same limit itself for any other caller).
CuttingJobBatchEntries = Annotated[
    list[CuttingJobBatchEntryIn], Field(min_length=1, max_length=MAX_TESTS_PER_BATCH)
]


class CuttingJobBatchErrorOut(BaseModel):
    """Why one Test in a batch got no job: the same status code, `detail`
    and (ticket #96) `code` that Test alone would get from
    `POST /cutting-jobs`."""

    status_code: int
    detail: str
    code: str | None


class CuttingJobBatchResultOut(BaseModel):
    """Exactly one of `job`/`error` is set."""

    test_id: str
    job: CuttingJobOut | None
    error: CuttingJobBatchErrorOut | None


class CuttingJobBatchOut(BaseModel):
    results: list[CuttingJobBatchResultOut]
