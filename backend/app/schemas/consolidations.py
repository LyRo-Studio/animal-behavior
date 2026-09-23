from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.consolidation import (
    DISPLAY_NAME_MAX_LENGTH,
    ConsolidationCondition,
    ConsolidationStatus,
)


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


class RenameConsolidationRequest(BaseModel):
    """Ticket #117: set (or clear) a Consolidation's user-facing label.
    `display_name` is required but nullable — `null` or a blank string
    clears the label, so the frontend falls back to `original_filename`.
    `original_filename` itself is immutable and not an accepted field
    (`extra="forbid"`: an attempt to send it is a 422, not silently
    ignored)."""

    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(max_length=DISPLAY_NAME_MAX_LENGTH)

    @field_validator("display_name", mode="before")
    @classmethod
    def _blank_clears(cls, value: object) -> object:
        # Trimmed before the length check, so surrounding whitespace never
        # counts against it or gets stored.
        if isinstance(value, str):
            return value.strip() or None
        return value
