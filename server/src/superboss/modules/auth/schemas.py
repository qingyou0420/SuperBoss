"""Auth public data transfer objects."""

from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator

from superboss.modules.auth.passwords import PasswordPolicyError, validate_password
from superboss.modules.users.models import Role

USERNAME_PATTERN = r"^[a-z][a-z0-9._-]{2,31}$"


class LoginCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    username: StrictStr = Field(min_length=3, max_length=32, pattern=USERNAME_PATTERN)
    password: StrictStr = Field(min_length=12, max_length=128)

    @field_validator("password")
    @classmethod
    def valid_password(cls, value: str) -> str:
        try:
            validate_password(value)
        except PasswordPolicyError as error:
            raise ValueError("invalid password") from error
        return value


class PasswordChangeCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    current_password: StrictStr = Field(min_length=12, max_length=128)
    new_password: StrictStr = Field(min_length=12, max_length=128)

    @field_validator("current_password", "new_password")
    @classmethod
    def valid_password(cls, value: str) -> str:
        try:
            validate_password(value)
        except PasswordPolicyError as error:
            raise ValueError("invalid password") from error
        return value


class AuthUserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    username: str
    display_name: str
    role: Role
    must_change_password: bool


@dataclass(frozen=True)
class SessionPair:
    access_token: str
    refresh_token: str
    access_expires_at: datetime
    refresh_expires_at: datetime
