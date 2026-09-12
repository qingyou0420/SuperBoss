"""Knowledge persistence."""

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
    String,
    Text,
    UniqueConstraint,
    false,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.schema import Computed

from superboss.core.db import Base


class KnowledgeStatus(StrEnum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"


class KnowledgeDoc(Base):
    __tablename__ = "knowledge_docs"
    __table_args__ = (
        CheckConstraint("status IN ('DRAFT','PUBLISHED')", name="ck_knowledge_docs_status"),
        CheckConstraint("char_length(title) BETWEEN 1 AND 255", name="ck_knowledge_docs_title"),
        Index("ix_knowledge_docs_status", "status"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body_md: Mapped[str] = mapped_column(Text, default="", nullable=False)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(64)), default=list, nullable=False)
    status: Mapped[KnowledgeStatus] = mapped_column(
        Enum(
            KnowledgeStatus,
            name="knowledge_status",
            native_enum=False,
            values_callable=lambda items: [item.value for item in items],
        ),
        default=KnowledgeStatus.DRAFT,
        nullable=False,
    )
    source_file_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    project_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    stage_title: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    change_reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    is_canonical: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    published_revision_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    draft_revision_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    created_by: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    search: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('simple', coalesce(title,'') || ' ' || coalesce(body_md,''))",
            persisted=True,
        ),
    )
    points: Mapped[list["KnowledgePoint"]] = relationship(
        back_populates="doc", cascade="all, delete-orphan", order_by="KnowledgePoint.sort_order"
    )
    revisions: Mapped[list["KnowledgeRevision"]] = relationship(
        back_populates="doc",
        cascade="all, delete-orphan",
        order_by="KnowledgeRevision.version",
    )


class KnowledgePoint(Base):
    __tablename__ = "knowledge_points"
    __table_args__ = (Index("ix_knowledge_points_doc", "doc_id"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    doc_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("knowledge_docs.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body_md: Mapped[str] = mapped_column(Text, default="", nullable=False)
    source_file_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    doc: Mapped[KnowledgeDoc] = relationship(back_populates="points")


class KnowledgeRevision(Base):
    __tablename__ = "knowledge_revisions"
    __table_args__ = (
        UniqueConstraint("doc_id", "version", name="uq_knowledge_revisions_doc_version"),
        Index("ix_knowledge_revisions_doc", "doc_id", "version"),
        CheckConstraint(
            "points_review IN ('OK','NEEDS_REVIEW','CLEARED')",
            name="ck_knowledge_revisions_points_review",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    doc_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("knowledge_docs.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    body_md: Mapped[str] = mapped_column(
        Text, default="", server_default=text("''"), nullable=False
    )
    change_reason: Mapped[str] = mapped_column(
        Text, default="", server_default=text("''"), nullable=False
    )
    stage_title: Mapped[str] = mapped_column(
        String(255), default="", server_default=text("''"), nullable=False
    )
    is_canonical: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )
    source_file_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    points_json: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb"), nullable=False
    )
    released: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )
    points_review: Mapped[str] = mapped_column(
        String(16), default="OK", server_default=text("'OK'"), nullable=False
    )
    points_reviewed_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    points_reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    doc: Mapped[KnowledgeDoc] = relationship(back_populates="revisions")


class KnowledgeRevisionPollution(Base):
    __tablename__ = "knowledge_revision_pollution"

    revision_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("knowledge_revisions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    reason: Mapped[str] = mapped_column(
        Text,
        default="EXPLICIT_POLLUTION",
        server_default=text("'EXPLICIT_POLLUTION'"),
        nullable=False,
    )
    original_points_json: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb"), nullable=False
    )
    reviewed_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class KnowledgeRevisionReviewEvent(Base):
    __tablename__ = "knowledge_revision_review_events"
    __table_args__ = (
        Index(
            "ix_knowledge_revision_review_events_revision",
            "revision_id",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    revision_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("knowledge_revisions.id", ondelete="CASCADE"),
        nullable=False,
    )
    doc_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("knowledge_docs.id", ondelete="CASCADE"), nullable=False
    )
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    previous_status: Mapped[str] = mapped_column(String(16), nullable=False)
    new_status: Mapped[str] = mapped_column(String(16), nullable=False)
    original_points_json: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
