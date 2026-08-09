"""Bounded HTTP schemas for device pairing and management."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PairingCodeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_ids: list[UUID] = Field(min_length=1)

    @field_validator("project_ids")
    @classmethod
    def unique_projects(cls, value: list[UUID]) -> list[UUID]:
        if len(set(value)) != len(value):
            raise ValueError("Project IDs must be unique")
        return value


class PairingCodeRead(BaseModel):
    raw_code: str
    expires_at: datetime


class DevicePair(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pairing_code: str
    device_name: str


class DeviceRefresh(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refresh_token: str


class DeviceTokenRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    access_token: str
    refresh_token: str
    token_type: str
    expires_at: datetime
    refresh_expires_at: datetime


class DeviceProjectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str


class DeviceMeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    scopes: tuple[str, ...]
    projects: tuple[DeviceProjectRead, ...]
    paired_at: datetime
    last_used_at: datetime | None


class OwnerDeviceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    paired_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None
    status: str
    projects: tuple[DeviceProjectRead, ...]
