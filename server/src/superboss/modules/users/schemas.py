"""Strict OWNER-to-STAFF account-management HTTP contracts."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator

from superboss.modules.auth.schemas import USERNAME_PATTERN
from superboss.modules.users.models import Role, UserStatus


class StaffCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: StrictStr = Field(min_length=3, max_length=32, pattern=USERNAME_PATTERN)
    display_name: StrictStr = Field(min_length=1, max_length=255)
    project_ids: list[UUID] = Field(max_length=1000)

    @field_validator("project_ids")
    @classmethod
    def unique_project_ids(cls, value: list[UUID]) -> list[UUID]:
        if len(set(value)) != len(value):
            raise ValueError("Project IDs must be unique")
        return value


class StaffUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: StrictStr | None = Field(default=None, min_length=1, max_length=255)
    status: UserStatus | None = None


class ProjectAssignments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_ids: list[UUID] = Field(max_length=1000)

    @field_validator("project_ids")
    @classmethod
    def unique_project_ids(cls, value: list[UUID]) -> list[UUID]:
        if len(set(value)) != len(value):
            raise ValueError("Project IDs must be unique")
        return value


class UserProjectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str


class OwnerUserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    username: str
    display_name: str
    role: Role
    status: UserStatus
    last_login_at: datetime | None
    projects: tuple[UserProjectRead, ...]


class StaffCreateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user: OwnerUserRead
    temporary_password: str


class PasswordResetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    temporary_password: str
