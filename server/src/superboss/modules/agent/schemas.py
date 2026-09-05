"""Agent HTTP and card payload schemas."""

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from superboss.modules.agent.models import (
    CardKind,
    CardStatus,
    MemoryKind,
    MemoryStatus,
    MessageRole,
)
from superboss.modules.finance.models import FinanceKind, FinanceScope, FinanceVisibility
from superboss.modules.finance.schemas import AdjustableField
from superboss.modules.knowledge.schemas import KnowledgeIngestCard
from superboss.modules.projects.models import ProjectStage
from superboss.modules.projects.schemas import MilestoneWrite


def _text(value: str, maximum: int, minimum: int = 1) -> str:
    normalized = value.strip(" \t\r\n\u00a0")
    if not minimum <= len(normalized) <= maximum:
        raise ValueError("text length is invalid")
    return normalized


class FinanceEntryCard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: FinanceKind
    scope: FinanceScope
    project_id: UUID | None = None
    amount_cents: int = Field(ge=1, le=1_000_000_000_000)
    occurred_on: date
    category: str
    memo: str = ""
    visibility: FinanceVisibility | None = None

    @field_validator("category")
    @classmethod
    def category_text(cls, value: str) -> str:
        return _text(value, 64)

    @field_validator("memo")
    @classmethod
    def memo_text(cls, value: str) -> str:
        return value.strip(" \t\r\n\u00a0")[:1000]


class FinanceAdjustCard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry_id: UUID
    field: AdjustableField
    new_value: str
    reason: str

    @field_validator("new_value", "reason")
    @classmethod
    def required_text(cls, value: str) -> str:
        return _text(value, 500)


class ProjectCreateCard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    stage: ProjectStage = ProjectStage.PLANNING
    milestones: list[MilestoneWrite] = Field(default_factory=list, max_length=100)

    @field_validator("name")
    @classmethod
    def name_text(cls, value: str) -> str:
        return _text(value, 255)

    @field_validator("description")
    @classmethod
    def description_text(cls, value: str) -> str:
        return value.strip(" \t\r\n\u00a0")[:4000]


class ProjectUpdateCard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: UUID
    name: str | None = None
    description: str | None = None
    stage: ProjectStage | None = None
    progress_percent: int | None = Field(default=None, ge=0, le=100)
    starts_on: date | None = None
    due_on: date | None = None


class MilestoneChangeUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    title: str | None = None
    due_on: date | None = None
    done: bool | None = None


class MilestoneChangeCard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: UUID
    add: list[MilestoneWrite] = Field(default_factory=list)
    update: list[MilestoneChangeUpdate] = Field(default_factory=list)
    remove: list[UUID] = Field(default_factory=list)


class FileMoveCard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_id: UUID
    target_folder_id: UUID
    new_name: str | None = None


class MemoryCard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: MemoryKind
    content: str
    importance: int = Field(default=3, ge=1, le=5)
    pinned: bool = False

    @field_validator("content")
    @classmethod
    def content_text(cls, value: str) -> str:
        return _text(value, 2000)


CARD_MODELS = {
    CardKind.FINANCE_ENTRY: FinanceEntryCard,
    CardKind.FINANCE_ADJUST: FinanceAdjustCard,
    CardKind.PROJECT_CREATE: ProjectCreateCard,
    CardKind.PROJECT_UPDATE: ProjectUpdateCard,
    CardKind.MILESTONE_CHANGE: MilestoneChangeCard,
    CardKind.FILE_MOVE: FileMoveCard,
    CardKind.MEMORY: MemoryCard,
    CardKind.KNOWLEDGE_INGEST: KnowledgeIngestCard,
}


class ConversationCreate(BaseModel):
    title: str | None = None


class ChatMessageCreate(BaseModel):
    content: str = ""
    file_id: UUID | None = None

    @field_validator("content")
    @classmethod
    def content_text(cls, value: str) -> str:
        return value.strip(" \t\r\n\u00a0")[:8000]

    @model_validator(mode="after")
    def require_content_or_file(self) -> "ChatMessageCreate":
        if not self.content and self.file_id is None:
            raise ValueError("content or file_id is required")
        return self


class CardRevise(BaseModel):
    instruction: str = Field(min_length=1, max_length=2000)

    @field_validator("instruction")
    @classmethod
    def instruction_text(cls, value: str) -> str:
        return _text(value, 2000)


class CardPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payload: dict[str, object]
    note: str = ""

    @field_validator("note")
    @classmethod
    def note_text(cls, value: str) -> str:
        return value.strip(" \t\r\n\u00a0")[:500]


class SoulWrite(BaseModel):
    content: str = Field(min_length=1, max_length=20_000)
    note: str = ""

    @field_validator("content")
    @classmethod
    def content_text(cls, value: str) -> str:
        return _text(value, 20_000, minimum=1)

    @field_validator("note")
    @classmethod
    def note_text(cls, value: str) -> str:
        return value.strip(" \t\r\n\u00a0")[:255]


class MemoryPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str | None = None
    pinned: bool | None = None
    status: MemoryStatus | None = None
    importance: int | None = Field(default=None, ge=1, le=5)

    @field_validator("content")
    @classmethod
    def content_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _text(value, 2000)


class ConversationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    summary: str
    created_at: datetime
    last_message_at: datetime
    archived_at: datetime | None


class CardRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    conversation_id: UUID
    message_id: UUID | None
    kind: CardKind
    payload: dict[str, object]
    status: CardStatus
    decided_at: datetime | None
    committed_object_type: str | None
    committed_object_id: UUID | None
    error: str | None


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    role: MessageRole
    content: str
    card_ids: list[UUID]
    created_at: datetime


class ChatTurnRead(BaseModel):
    conversation_id: UUID
    message: MessageRead
    cards: list[CardRead]
    offline: bool = False


class SoulRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    content: str
    note: str
    created_at: datetime
    is_active: bool


class SoulPreview(BaseModel):
    prompt: str


class MemoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    kind: MemoryKind
    content: str
    importance: int
    pinned: bool
    status: MemoryStatus
    created_at: datetime
    recall_count: int
