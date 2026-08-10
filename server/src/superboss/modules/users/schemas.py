"""Strict OWNER-to-STAFF account-management HTTP contracts."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator

from superboss.modules.users.models import Role, UserStatus


class StaffCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    wecom_userid: StrictStr = Field(min_length=1, max_length=255)
    display_name: StrictStr = Field(min_length=1, max_length=255)
    project_ids: list[UUID]

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

    @field_validator("display_name")
    @classmethod
    def ensure_update_present(cls, value: StrictStr | None) -> StrictStr | None:
        return value


class ProjectAssignments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_ids: list[UUID]

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
    wecom_userid: str
    display_name: str
    role: Role
    status: UserStatus
    last_login_at: datetime | None
    projects: tuple[UserProjectRead, ...]
