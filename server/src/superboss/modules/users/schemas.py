"""Strict OWNER-to-STAFF account-management HTTP contracts."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator

from superboss.modules.auth.schemas import USERNAME_PATTERN
from superboss.modules.users.models import Role, UserStatus

_MANAGED_ROLES = frozenset({Role.MANAGER, Role.STAFF})


class StaffCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: StrictStr = Field(min_length=3, max_length=32, pattern=USERNAME_PATTERN)
    display_name: StrictStr = Field(min_length=1, max_length=255)
    role: Role = Role.STAFF

    @field_validator("role")
    @classmethod
    def managed_role(cls, value: Role) -> Role:
        if value not in _MANAGED_ROLES:
            raise ValueError("Role must be MANAGER or STAFF")
        return value


class StaffUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: StrictStr | None = Field(default=None, min_length=1, max_length=255)
    status: UserStatus | None = None
    role: Role | None = None

    @field_validator("role")
    @classmethod
    def managed_role(cls, value: Role | None) -> Role | None:
        if value is not None and value not in _MANAGED_ROLES:
            raise ValueError("Role must be MANAGER or STAFF")
        return value


class OwnerUserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    username: str
    display_name: str
    role: Role
    status: UserStatus
    last_login_at: datetime | None


class StaffCreateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user: OwnerUserRead
    temporary_password: str


class PasswordResetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    temporary_password: str
