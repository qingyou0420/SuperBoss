"""File upload persistence state."""

from datetime import date, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from superboss.core.db import Base


class FileState(StrEnum):
    UPLOADING = "UPLOADING"
    QUARANTINED = "QUARANTINED"
    SCANNING = "SCANNING"
    CLEAN = "CLEAN"
    INFECTED = "INFECTED"
    FAILED = "FAILED"


class File(Base):
    __tablename__ = "files"
    __table_args__ = (
        UniqueConstraint("id", "project_id", name="uq_files_id_project"),
        CheckConstraint(
            "state IN ('UPLOADING','QUARANTINED','SCANNING','CLEAN','INFECTED','FAILED')",
            name="ck_files_state",
        ),
        CheckConstraint("size_bytes BETWEEN 1 AND 104857600", name="ck_files_size"),
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="ck_files_sha256"),
        CheckConstraint("uploader_kind IN ('user','device','system')", name="ck_files_uploader_kind"),
        CheckConstraint("char_length(filename) > 0", name="ck_files_filename"),
        CheckConstraint("char_length(category) > 0", name="ck_files_category"),
        CheckConstraint("char_length(object_key) > 0", name="ck_files_object_key"),
        CheckConstraint("char_length(content_type) > 0", name="ck_files_content_type"),
    )
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    filename: Mapped[str] = mapped_column(String(1024), nullable=False)
    category: Mapped[str] = mapped_column(String(255), nullable=False)
    file_date: Mapped[date] = mapped_column(Date, nullable=False)
    object_key: Mapped[str] = mapped_column(String(2048), unique=True, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[FileState] = mapped_column(
        Enum(FileState, name="file_state", native_enum=False),
        default=FileState.UPLOADING,
        nullable=False,
    )
    uploader_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    uploader_kind: Mapped[str] = mapped_column(String(16), default="user", nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    scan_result: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Upload(Base):
    __tablename__ = "uploads"
    __table_args__ = (
        ForeignKeyConstraint(
            ["file_id", "project_id"],
            ["files.id", "files.project_id"],
            name="fk_uploads_file_project",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "project_id",
            "uploader_kind",
            "uploader_id",
            "idempotency_key",
            name="uq_upload_idempotency",
        ),
        CheckConstraint("uploader_kind IN ('user','device','system')", name="ck_uploads_uploader_kind"),
        CheckConstraint("char_length(idempotency_key) > 0", name="ck_uploads_idempotency_key"),
        CheckConstraint("metadata_fingerprint ~ '^[0-9a-f]{64}$'", name="ck_uploads_fingerprint"),
        CheckConstraint("char_length(multipart_id) > 0", name="ck_uploads_multipart_id_not_empty"),
    )
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    file_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        unique=True,
        nullable=False,
    )
    project_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    uploader_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    uploader_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    metadata_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    multipart_id: Mapped[str] = mapped_column(String(1024), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
