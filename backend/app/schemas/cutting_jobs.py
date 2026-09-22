from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from app.models.cutting_job import CuttingJobOutputStatus, CuttingJobStatus

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
