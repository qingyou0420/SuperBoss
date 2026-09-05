"""Knowledge HTTP and card schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from superboss.modules.knowledge.models import KnowledgeStatus


def _text(value: str, maximum: int) -> str:
    normalized = value.strip(" \t\r\n\u00a0")
    if not 1 <= len(normalized) <= maximum:
        raise ValueError("text length is invalid")
    return normalized


class KnowledgePointWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    body_md: str = ""

    @field_validator("title")
    @classmethod
    def title_text(cls, value: str) -> str:
        return _text(value, 255)

    @field_validator("body_md")
    @classmethod
    def body_text(cls, value: str) -> str:
        return value.strip(" \t\r\n\u00a0")[:20_000]


class KnowledgeDocCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    body_md: str = ""
    tags: list[str] = Field(default_factory=list, max_length=20)
    points: list[KnowledgePointWrite] = Field(default_factory=list, max_length=100)

    @field_validator("title")
    @classmethod
    def title_text(cls, value: str) -> str:
        return _text(value, 255)


class KnowledgeDocUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    body_md: str | None = None
    tags: list[str] | None = None
    status: KnowledgeStatus | None = None


class KnowledgeIngestCard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_file_id: UUID | None = None
    target_doc_id: UUID | None = None
    new_doc_title: str | None = None
    tags: list[str] = Field(default_factory=list)
    points: list[KnowledgePointWrite] = Field(min_length=1, max_length=50)


class KnowledgePointRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    body_md: str
    sort_order: int


class KnowledgeDocRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    body_md: str
    tags: list[str]
    status: KnowledgeStatus
    updated_at: datetime
    points: list[KnowledgePointRead] = ()
