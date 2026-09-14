from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.account import AccountRole


class AdminCreateAccountRequest(BaseModel):
    email: EmailStr = Field(max_length=320)


class AdminAccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    display_name: str
    role: AccountRole
    is_active: bool
