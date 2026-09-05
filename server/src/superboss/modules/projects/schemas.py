"""Project HTTP schemas."""

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

from superboss.modules.projects.models import ProjectStage, ProjectStatus

PROJECT_EDGE_WHITESPACE = " \t\r\n\u00a0"


def _canonical_text(value: str, *, maximum: int) -> str:
    normalized = value.strip(PROJECT_EDGE_WHITESPACE)
    if not 1 <= len(normalized) <= maximum:
        raise ValueError("text must contain 1 to the allowed number of characters")
    return normalized


class MilestoneWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    due_on: date | None = None
    done: bool = False
    sort_order: int = Field(default=0, ge=0, le=10_000)

    @field_validator("title")
    @classmethod
    def canonical_title(cls, value: str) -> str:
        return _canonical_text(value, maximum=255)


class MilestoneReplace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    milestones: list[MilestoneWrite] = Field(max_length=100)


class MilestoneRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    due_on: date | None
    done_at: datetime | None
    sort_order: int


class ProjectCreate(BaseModel):
    name: str
    is_test: StrictBool = False
    description: str = ""
    stage: ProjectStage = ProjectStage.PLANNING
    starts_on: date | None = None
    due_on: date | None = None

    @field_validator("name")
    @classmethod
    def canonical_name(cls, value: str) -> str:
        return _canonical_text(value, maximum=255)

    @field_validator("description")
    @classmethod
    def canonical_description(cls, value: str) -> str:
        return value.strip(PROJECT_EDGE_WHITESPACE)[:4000]


class ProjectUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    description: str | None = None
    stage: ProjectStage | None = None
    progress_percent: int | None = Field(default=None, ge=0, le=100)
    starts_on: date | None = None
    due_on: date | None = None

    @field_validator("name")
    @classmethod
    def canonical_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _canonical_text(value, maximum=255)

    @field_validator("description")
    @classmethod
    def canonical_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip(PROJECT_EDGE_WHITESPACE)[:4000]


class ProjectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str
    is_test: bool
    status: ProjectStatus
    stage: ProjectStage
    progress_percent: int
    starts_on: date | None
    due_on: date | None
    milestones: tuple[MilestoneRead, ...] = ()
