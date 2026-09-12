"""Directory HTTP schemas."""

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from superboss.modules.directory.models import CommunicationStatus

_EDGE = " \t\r\n\u00a0"


def _text(value: str, maximum: int) -> str:
    return value.strip(_EDGE)[:maximum]


class DirectoryEntryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    district: str
    street: str
    community: str
    property_company: str
    households: int | None
    floor_area: str
    delivered_on: date | None
    estate_type: str
    manager_name: str
    phone_masked: str
    phone: str | None = None
    source_filename: str
    source_sheet: str
    source_row: int
    extra: dict[str, object]
    project_id: UUID | None = None


class ConvertProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = ""
    starts_on: date | None = None
    service_fee_cents: int | None = Field(default=None, ge=0)
    lead_user_id: UUID | None = None
    new_engagement: bool = False

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        return _text(value, 255)


class DirectoryListRead(BaseModel):
    items: list[DirectoryEntryRead]
    total: int


class CommunicationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    occurred_on: date
    contact_name: str
    contact_role: str
    demand: str
    result: str
    next_step: str = ""
    status: CommunicationStatus = CommunicationStatus.LEARNING

    @field_validator("contact_name", "contact_role", "demand", "result", "next_step")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return _text(value, 4000)


class CommunicationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    entry_id: UUID
    occurred_on: date
    contact_name: str
    contact_role: str
    demand: str
    result: str
    next_step: str
    status: CommunicationStatus
    created_at: datetime


class ImportResult(BaseModel):
    inserted: int
    updated: int
    skipped: int
    source_filename: str = Field(max_length=255)
    conflicts: list[dict[str, object]] = Field(default_factory=list)


class DirectoryConflictRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    reason: str
    payload: dict[str, object]
    existing_entry_id: UUID | None = None
    source_row_id: UUID | None = None
    batch_id: UUID | None = None
    status: str = "OPEN"
    resolved_entry_id: UUID | None = None
    created_at: datetime


class DirectoryConflictListRead(BaseModel):
    items: list[DirectoryConflictRead]
    total: int


class DirectoryConflictResolve(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str

    @field_validator("action")
    @classmethod
    def canonical_action(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if cleaned not in {"link", "split"}:
            raise ValueError("action must be link or split")
        return cleaned


class DirectoryFacets(BaseModel):
    districts: list[str]
    streets: list[str]
    streets_by_district: dict[str, list[str]] = Field(default_factory=dict)


class DirectoryProjectLinkRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    is_current: bool
    created_at: datetime
