from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.consolidation import ConsolidationCondition, ConsolidationStatus


class ConsolidationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    original_filename: str
    display_name: str | None
    condition: ConsolidationCondition
    status: ConsolidationStatus
    requested_by_identity: str | None
    failure_reason: str | None
    input_size_bytes: int
    result_size_bytes: int | None
    created_at: datetime
    completed_at: datetime | None
