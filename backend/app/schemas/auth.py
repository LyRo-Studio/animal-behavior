from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    # Bounded per ENGINEERING-STANDARDS.md's DoS-protection guidance:
    # unbounded input feeding Argon2 (deliberately CPU-expensive) hashing
    # would otherwise be a cheap amplification lever.
    email: EmailStr = Field(max_length=320)
    password: str = Field(min_length=1, max_length=128)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1, max_length=512)


class LogoutRequest(BaseModel):
    refresh_token: str = Field(min_length=1, max_length=512)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
