"""Business-directory persistence."""

from datetime import date, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from superboss.core.db import Base


class CommunicationStatus(StrEnum):
    LEARNING = "LEARNING"
    CONTACTING = "CONTACTING"
    COMMISSIONED = "COMMISSIONED"
    PAUSED = "PAUSED"
    DROPPED = "DROPPED"


class DirectoryEntry(Base):
    __tablename__ = "directory_entries"
    __table_args__ = (
        CheckConstraint(
            "char_length(name) BETWEEN 1 AND 255", name="ck_directory_entries_name_length"
        ),
        CheckConstraint("source_row >= 1", name="ck_directory_entries_source_row"),
        Index("ix_directory_entries_area", "district", "street", "community"),
        Index("ix_directory_entries_identity", "identity_key"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    district: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    street: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    community: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    property_company: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    households: Mapped[int | None] = mapped_column(Integer, nullable=True)
    floor_area: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    delivered_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    estate_type: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    manager_name: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    phone: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    extra: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, nullable=False)
    source_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    source_sheet: Mapped[str] = mapped_column(String(64), default="Sheet1", nullable=False)
    source_row: Mapped[int] = mapped_column(Integer, nullable=False)
    identity_key: Mapped[str] = mapped_column(
        String(512), default="", server_default=text("''"), nullable=False
    )
    project_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    project_links: Mapped[list["DirectoryProjectLink"]] = relationship(
        back_populates="entry",
        cascade="all, delete-orphan",
        order_by="DirectoryProjectLink.created_at.desc()",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    communications: Mapped[list["DirectoryCommunication"]] = relationship(
        back_populates="entry",
        cascade="all, delete-orphan",
    )


class DirectoryCommunication(Base):
    __tablename__ = "directory_communications"
    __table_args__ = (
        CheckConstraint(
            "status IN ('LEARNING','CONTACTING','COMMISSIONED','PAUSED','DROPPED')",
            name="ck_directory_communications_status",
        ),
        Index("ix_directory_communications_entry", "entry_id", "occurred_on"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    entry_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("directory_entries.id", ondelete="CASCADE"),
        nullable=False,
    )
    occurred_on: Mapped[date] = mapped_column(Date, nullable=False)
    contact_name: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    contact_role: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    demand: Mapped[str] = mapped_column(Text, default="", nullable=False)
    result: Mapped[str] = mapped_column(Text, default="", nullable=False)
    next_step: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[CommunicationStatus] = mapped_column(
        Enum(
            CommunicationStatus,
            name="directory_communication_status",
            native_enum=False,
            values_callable=lambda items: [item.value for item in items],
        ),
        default=CommunicationStatus.LEARNING,
        nullable=False,
    )
    created_by: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    entry: Mapped[DirectoryEntry] = relationship(back_populates="communications")


class DirectoryImportBatch(Base):
    __tablename__ = "directory_import_batches"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    created_by: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    source_rows: Mapped[list["DirectorySourceRow"]] = relationship(
        back_populates="batch", cascade="all, delete-orphan"
    )
    conflicts: Mapped[list["DirectoryConflict"]] = relationship(
        back_populates="batch", cascade="all, delete-orphan"
    )


class DirectorySourceRow(Base):
    __tablename__ = "directory_source_rows"
    __table_args__ = (Index("ix_directory_source_rows_batch", "batch_id", "row_number"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    batch_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("directory_import_batches.id", ondelete="CASCADE"),
        nullable=False,
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    sheet: Mapped[str] = mapped_column(
        String(64), default="Sheet1", server_default=text("'Sheet1'"), nullable=False
    )
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    identity_key: Mapped[str] = mapped_column(
        String(512), default="", server_default=text("''"), nullable=False
    )
    payload: Mapped[dict[str, object]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )
    entry_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("directory_entries.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    batch: Mapped[DirectoryImportBatch] = relationship(back_populates="source_rows")


class DirectoryConflict(Base):
    __tablename__ = "directory_conflicts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('OPEN','LINKED','SPLIT','SUPERSEDED')",
            name="ck_directory_conflicts_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    batch_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("directory_import_batches.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_row_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("directory_source_rows.id", ondelete="SET NULL"),
        nullable=True,
    )
    existing_entry_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("directory_entries.id", ondelete="SET NULL"),
        nullable=True,
    )
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(16), default="OPEN", server_default=text("'OPEN'"), nullable=False
    )
    resolved_entry_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("directory_entries.id", ondelete="SET NULL"),
        nullable=True,
    )
    resolved_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    batch: Mapped[DirectoryImportBatch] = relationship(back_populates="conflicts")


class DirectoryProjectLink(Base):
    __tablename__ = "directory_project_links"
    __table_args__ = (
        UniqueConstraint("entry_id", "project_id", name="uq_directory_project_links"),
        Index("ix_directory_project_links_entry", "entry_id", "is_current"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    entry_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("directory_entries.id", ondelete="CASCADE"),
        nullable=False,
    )
    project_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    is_current: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true(), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    entry: Mapped[DirectoryEntry] = relationship(back_populates="project_links")
