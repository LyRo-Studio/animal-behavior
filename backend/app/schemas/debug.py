from pydantic import BaseModel


class RecentMediaRequestOut(BaseModel):
    at: str
    sec_fetch_dest: str | None
    identity_header_present: bool
    header_names: list[str]


class RequestHeadersOut(BaseModel):
    identity_header_name: str | None
    identity_header_present: bool
    headers: dict[str, str]
    recent_media_requests: list[RecentMediaRequestOut]
