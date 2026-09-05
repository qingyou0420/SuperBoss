"""Knowledge persistence."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, TSVECTOR
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
