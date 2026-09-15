from datetime import datetime

from pydantic import BaseModel, ConfigDict


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
