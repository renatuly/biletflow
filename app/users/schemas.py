from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str = Field(min_length=2, max_length=200)
    preferred_locale: str = Field(default="kk", pattern="^(kk|ru|en)$")


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=200)
    phone: str | None = Field(default=None, max_length=40)
    preferred_locale: str | None = Field(default=None, pattern="^(kk|ru|en)$")


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr
    full_name: str | None
    phone: str | None
    preferred_locale: str
    roles: list[str]
    email_verified: bool
    is_active: bool
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserRead


class TokenRequest(BaseModel):
    token: str


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    token: str
    password: str = Field(min_length=8, max_length=128)
