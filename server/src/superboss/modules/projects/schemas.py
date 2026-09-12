"""Project HTTP schemas."""

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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
    description: str = ""
    stage: ProjectStage = ProjectStage.PLANNING
    starts_on: date | None = None
    due_on: date | None = None
    service_fee_cents: int | None = Field(default=None, ge=0)
    lead_user_id: UUID | None = None
    apply_workflow: bool = True

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
    contract_due_on: date | None = None
    service_fee_cents: int | None = Field(default=None, ge=0)
    lead_user_id: UUID | None = None

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


class NodeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    sort_order: int
    title: str
    planned_start: date | None
    planned_end: date | None
    status: str
    completed_at: datetime | None
    completed_by: UUID | None = None
    preparation: list[object] = Field(default_factory=list)
    document_name: str
    photo_required: bool
    required_materials: list[object] = Field(default_factory=list)
    duration_days: int = 1
    evidence: list[object] = Field(default_factory=list)


class ScheduleChangeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    node_id: UUID | None
    days: int
    reason: str
    created_at: datetime
    created_by: UUID | None = None
    before_json: list[object] = Field(default_factory=list)
    after_json: list[object] = Field(default_factory=list)


class ScheduleShift(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: UUID
    days: int = Field(..., ge=-365, le=365)
    reason: str = ""


class WorkflowApply(BaseModel):
    model_config = ConfigDict(extra="forbid")

    starts_on: date | None = None
    template_version_id: UUID | None = None


class ScheduleFromDate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: UUID
    new_start: date
    reason: str = ""


class TemplateNodeWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    duration_days: int = Field(default=1, ge=1, le=365)
    photo_required: bool = False
    document_name: str = ""
    preparation: list[str] = Field(default_factory=list)
    required_materials: list[str] = Field(default_factory=list)

    @field_validator("title")
    @classmethod
    def canonical_title(cls, value: str) -> str:
        return _canonical_text(value, maximum=255)


class TemplateWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = "业主大会默认流程"
    nodes: list[TemplateNodeWrite] = Field(min_length=1, max_length=40)


class NodeComplete(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence: list[str] = Field(default_factory=list, max_length=20)


class ProjectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str
    status: ProjectStatus
    stage: ProjectStage
    progress_percent: int
    starts_on: date | None
    due_on: date | None
    contract_due_on: date | None = None
    service_completed_on: date | None = None
    template_version_id: UUID | None = None
    service_fee_cents: int | None = None
    lead_user_id: UUID | None = None
    workflow_pending: bool = False
    milestones: tuple[MilestoneRead, ...] = ()
    nodes: tuple[NodeRead, ...] = ()
    schedule_changes: tuple[ScheduleChangeRead, ...] = ()

    @model_validator(mode="after")
    def mark_workflow_pending(self) -> "ProjectRead":
        self.workflow_pending = len(self.nodes) == 0
        return self
