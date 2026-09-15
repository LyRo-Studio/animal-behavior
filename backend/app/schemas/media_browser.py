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
