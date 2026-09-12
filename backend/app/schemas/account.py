from pydantic import BaseModel, ConfigDict, EmailStr

from app.models.account import AccountRole


class AccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    display_name: str
    role: AccountRole
