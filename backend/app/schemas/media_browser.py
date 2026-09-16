import enum
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CutOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: str
    filename: str
    camera: str | None
    condition: str | None
    phase: str | None
    size: int
    last_modified: datetime


class DatasetEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    key: str
    is_folder: bool
    size: int | None
    last_modified: datetime | None


class MediaTokenAction(str, enum.Enum):
    """Ticket #21: a media token is scoped to exactly one of these — see
    docs/adr/0002-media-access-tokens-in-url.md."""

    PLAY = "play"
    DOWNLOAD = "download"


class MediaTokenRequest(BaseModel):
    # Bounded per ENGINEERING-STANDARDS.md's DoS-protection guidance, same
    # spirit as LoginRequest's field bounds — this feeds straight into a
    # regex match (parse_cut_key), never persisted or hashed, so the bound
    # only needs to be generous enough for any real Cut key.
    key: str = Field(min_length=1, max_length=1024)
    action: MediaTokenAction


class MediaTokenResponse(BaseModel):
    token: str
    expires_in: int


class CutMediaInfoOut(BaseModel):
    """Ticket #22: probed media info for a Cut, alongside the basic info
    `CutOut` already carries. Raw `width`/`height` rather than a
    pre-joined "1920x1080" string — same "backend sends raw fields, the
    frontend formats them for display" convention as `CutOut.size`/
    `last_modified`."""

    model_config = ConfigDict(from_attributes=True)

    duration_seconds: float
    width: int
    height: int
    codec: str
