from pydantic import BaseModel, ConfigDict, EmailStr, Field


class AdminCreateAccountRequest(BaseModel):
    email: EmailStr = Field(max_length=320)


class AdminAccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    display_name: str
    is_active: bool
