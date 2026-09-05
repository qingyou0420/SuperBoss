"""Agent persistence models."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.schema import Computed

from superboss.core.db import Base


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"
    SYSTEM = "system"


class CardKind(StrEnum):
    FINANCE_ENTRY = "finance_entry"
    FINANCE_ADJUST = "finance_adjust"
    PROJECT_CREATE = "project_create"
    PROJECT_UPDATE = "project_update"
    MILESTONE_CHANGE = "milestone_change"
    FILE_MOVE = "file_move"
    MEMORY = "memory"
    KNOWLEDGE_INGEST = "knowledge_ingest"


class CardStatus(StrEnum):
    PROPOSED = "PROPOSED"
    CONFIRMED = "CONFIRMED"
    COMMITTED = "COMMITTED"
    REVISED = "REVISED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


class MemoryKind(StrEnum):
    FACT = "FACT"
    PREFERENCE = "PREFERENCE"
    DECISION = "DECISION"
    PROJECT_NOTE = "PROJECT_NOTE"
    DAILY_DIGEST = "DAILY_DIGEST"


class MemoryStatus(StrEnum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class AgentConversation(Base):
    __tablename__ = "agent_conversations"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(80), default="新对话", nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_message_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    messages: Mapped[list["AgentMessage"]] = relationship(back_populates="conversation")
    cards: Mapped[list["AgentCard"]] = relationship(back_populates="conversation")


class AgentMessage(Base):
    __tablename__ = "agent_messages"
    __table_args__ = (
        CheckConstraint(
            "role IN ('user','assistant','tool','system')",
            name="ck_agent_messages_role",
        ),
        Index("ix_agent_messages_conversation", "conversation_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("agent_conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[MessageRole] = mapped_column(
        Enum(
            MessageRole,
            name="agent_message_role",
            native_enum=False,
            values_callable=lambda items: [item.value for item in items],
        ),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    tool_calls: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, nullable=False)
    card_ids: Mapped[list[UUID]] = mapped_column(
        ARRAY(PG_UUID(as_uuid=True)), default=list, nullable=False
    )
    token_usage: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    conversation: Mapped[AgentConversation] = relationship(back_populates="messages")


class AgentCard(Base):
    __tablename__ = "agent_cards"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PROPOSED','CONFIRMED','COMMITTED','REVISED','REJECTED','FAILED')",
            name="ck_agent_cards_status",
        ),
        Index("ix_agent_cards_conversation", "conversation_id"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("agent_conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    message_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("agent_messages.id", ondelete="SET NULL"), nullable=True
    )
    kind: Mapped[CardKind] = mapped_column(
        Enum(
            CardKind,
            name="agent_card_kind",
            native_enum=False,
            values_callable=lambda items: [item.value for item in items],
        ),
        nullable=False,
    )
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    status: Mapped[CardStatus] = mapped_column(
        Enum(
            CardStatus,
            name="agent_card_status",
            native_enum=False,
            values_callable=lambda items: [item.value for item in items],
        ),
        default=CardStatus.PROPOSED,
        nullable=False,
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    committed_object_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    committed_object_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    conversation: Mapped[AgentConversation] = relationship(back_populates="cards")


class AgentSoulVersion(Base):
    __tablename__ = "agent_soul_versions"
    __table_args__ = (
        Index(
            "uq_agent_soul_active",
            "is_active",
            unique=True,
            postgresql_where=text("is_active"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class AgentMemory(Base):
    __tablename__ = "agent_memories"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('FACT','PREFERENCE','DECISION','PROJECT_NOTE','DAILY_DIGEST')",
            name="ck_agent_memories_kind",
        ),
        CheckConstraint("status IN ('ACTIVE','ARCHIVED')", name="ck_agent_memories_status"),
        CheckConstraint("importance BETWEEN 1 AND 5", name="ck_agent_memories_importance"),
        Index("ix_agent_memories_status", "status"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    kind: Mapped[MemoryKind] = mapped_column(
        Enum(
            MemoryKind,
            name="agent_memory_kind",
            native_enum=False,
            values_callable=lambda items: [item.value for item in items],
        ),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source_message_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    importance: Mapped[int] = mapped_column(SmallInteger, default=3, nullable=False)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[MemoryStatus] = mapped_column(
        Enum(
            MemoryStatus,
            name="agent_memory_status",
            native_enum=False,
            values_callable=lambda items: [item.value for item in items],
        ),
        default=MemoryStatus.ACTIVE,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_recalled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    recall_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    search: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('simple', coalesce(content, ''))", persisted=True),
    )
