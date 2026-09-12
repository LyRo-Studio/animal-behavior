from pydantic import BaseModel, Field


class SetPasswordRequest(BaseModel):
    token: str = Field(min_length=1, max_length=512)
    # A minimum length is a reasonable baseline password policy; nothing in
    # the ticket/spec calls for more (e.g. character-class rules), so this
    # is deliberately the only constraint beyond the existing max_length
    # DoS bound used on LoginRequest.password.
    password: str = Field(min_length=8, max_length=128)
