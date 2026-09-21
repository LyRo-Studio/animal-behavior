from pydantic import BaseModel


class WhoAmIOut(BaseModel):
    identity: str | None
