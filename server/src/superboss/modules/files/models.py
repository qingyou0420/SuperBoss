"""File and folder persistence."""

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
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from superboss.core.db import Base


class FileState(StrEnum):
    UPLOADING = "UPLOADING"
    QUARANTINED = "QUARANTINED"
    SCANNING = "SCANNING"
    CLEAN = "CLEAN"
    INFECTED = "INFECTED"
    FAILED = "FAILED"


class FolderVisibility(StrEnum):
    ALL = "ALL"
    MANAGEMENT = "MANAGEMENT"
    OWNER_ONLY = "OWNER_ONLY"


class Folder(Base):
    __tablename__ = "folders"
    __table_args__ = (
        CheckConstraint(
            "visibility IN ('ALL','MANAGEMENT','OWNER_ONLY')",
            name="ck_folders_visibility",
        ),
        CheckConstraint(
            "name = btrim(name, E' \\t\\r\\n' || chr(160))",
            name="ck_folders_name_trimmed",
        ),
        CheckConstraint("char_length(name) BETWEEN 1 AND 128", name="ck_folders_name_length"),
        Index("ix_folders_parent", "parent_id"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    parent_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("folders.id", ondelete="CASCADE"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    visibility: Mapped[FolderVisibility] = mapped_column(
        Enum(FolderVisibility, name="folder_visibility", native_enum=False),
        nullable=False,
    )
    created_by: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    parent: Mapped["Folder | None"] = relationship(
        remote_side="Folder.id", back_populates="children"
    )
    children: Mapped[list["Folder"]] = relationship(back_populates="parent")


class File(Base):
    __tablename__ = "files"
    __table_args__ = (
        UniqueConstraint(
            "folder_id",
            "uploader_id",
            "idempotency_key",
            name="uq_files_upload_idempotency",
        ),
        CheckConstraint(
            "state IN ('UPLOADING','QUARANTINED','SCANNING','CLEAN','INFECTED','FAILED')",
            name="ck_files_state",
        ),
        CheckConstraint("size_bytes BETWEEN 1 AND 104857600", name="ck_files_size"),
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="ck_files_sha256"),
        CheckConstraint("char_length(filename) > 0", name="ck_files_filename"),
        CheckConstraint("char_length(object_key) > 0", name="ck_files_object_key"),
        CheckConstraint("char_length(content_type) > 0", name="ck_files_content_type"),
        CheckConstraint(
            "idempotency_key IS NULL OR char_length(idempotency_key) > 0",
            name="ck_files_idempotency_key",
        ),
        CheckConstraint(
            "metadata_fingerprint IS NULL OR metadata_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_files_fingerprint",
        ),
        CheckConstraint(
            "multipart_id IS NULL OR char_length(multipart_id) > 0",
            name="ck_files_multipart_id_empty_or_nonempty",
        ),
    )
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    folder_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("folders.id", ondelete="RESTRICT"), nullable=False
    )
    project_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    filename: Mapped[str] = mapped_column(String(1024), nullable=False)
    object_key: Mapped[str] = mapped_column(String(2048), unique=True, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[FileState] = mapped_column(
        Enum(FileState, name="file_state", native_enum=False),
        default=FileState.UPLOADING,
        nullable=False,
    )
    uploader_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    scan_result: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    multipart_id: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    folder: Mapped[Folder] = relationship()
