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
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
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
        UniqueConstraint("id", "file_id", "project_id", name="uq_uploads_id_file_project"),
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
        CheckConstraint(
            "multipart_id IS NULL OR char_length(multipart_id) > 0",
            name="ck_uploads_multipart_id_empty_or_nonempty",
        ),
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
    multipart_id: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class FileUploadLifecycle(Base):
    """Durable storage-operation intent, deliberately independent of cascaded rows."""

    __tablename__ = "file_upload_lifecycle"
    __table_args__ = (
        PrimaryKeyConstraint("upload_id", name="pk_file_upload_lifecycle"),
        UniqueConstraint("file_id", name="uq_file_upload_lifecycle_file_id"),
        UniqueConstraint("completion_event_key", name="uq_file_upload_lifecycle_completion_event"),
        CheckConstraint(
            "provision_state IN ('PROVISIONING','READY','CANCEL_REQUESTED','TERMINAL')",
            name="ck_file_upload_lifecycle_provision_state",
        ),
        CheckConstraint(
            "completion_state IN ('NONE','PREPARED','VERIFIED','QUARANTINED','COMPENSATION_PENDING')",
            name="ck_file_upload_lifecycle_completion_state",
        ),
        CheckConstraint(
            "declared_size_bytes BETWEEN 1 AND 104857600",
            name="ck_file_upload_lifecycle_size",
        ),
        CheckConstraint("char_length(object_key) > 0", name="ck_file_upload_lifecycle_object_key"),
        CheckConstraint(
            "char_length(content_type) > 0", name="ck_file_upload_lifecycle_content_type"
        ),
        CheckConstraint(
            "multipart_id IS NULL OR char_length(multipart_id) > 0",
            name="ck_file_upload_lifecycle_multipart_id",
        ),
        CheckConstraint(
            "parts_digest IS NULL OR parts_digest ~ '^[0-9a-f]{64}$'",
            name="ck_file_upload_lifecycle_parts_digest",
        ),
        CheckConstraint(
            "completion_attempt_count >= 0",
            name="ck_file_upload_lifecycle_completion_attempt_count",
        ),
        CheckConstraint(
            "completion_last_error_code IS NULL OR completion_last_error_code IN ('COMPLETION_AMBIGUOUS')",
            name="ck_file_upload_lifecycle_completion_error_code",
        ),
        Index(
            "ix_file_upload_lifecycle_nonterminal",
            "provision_state",
            "completion_state",
        ),
        Index(
            "ix_file_upload_lifecycle_completion_due",
            "completion_state",
            "completion_next_attempt_at",
        ),
    )
    upload_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    file_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    project_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    object_key: Mapped[str] = mapped_column(String(2048), nullable=False)
    multipart_id: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    declared_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    provision_state: Mapped[str] = mapped_column(
        String(32), server_default=text("'READY'"), nullable=False
    )
    completion_state: Mapped[str] = mapped_column(
        String(32), server_default=text("'NONE'"), nullable=False
    )
    parts_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    canonical_parts_json: Mapped[list[dict[str, object]] | None] = mapped_column(JSONB, nullable=True)
    completion_actor_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    completion_actor_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    completion_actor_role: Mapped[str | None] = mapped_column(String(16), nullable=True)
    completion_request_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    prepared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completion_event_key: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    completion_attempt_count: Mapped[int] = mapped_column(
        Integer, server_default=text("0"), nullable=False
    )
    completion_next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completion_last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class FileLifecycleOutbox(Base):
    """Durable external delivery work; payloads are stable scalar identifiers only."""

    __tablename__ = "file_lifecycle_outbox"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_file_lifecycle_outbox"),
        UniqueConstraint("kind", "dedupe_key", name="uq_file_lifecycle_outbox_kind_dedupe"),
        CheckConstraint(
            "kind IN ('scan_dispatch','completion_audit')", name="ck_file_lifecycle_outbox_kind"
        ),
        CheckConstraint(
            "state IN ('PENDING','DELIVERING','DELIVERED')", name="ck_file_lifecycle_outbox_state"
        ),
        CheckConstraint("attempt_count >= 0", name="ck_file_lifecycle_outbox_attempt_count"),
        Index("ix_file_lifecycle_outbox_pending", "state", "next_attempt_at"),
        Index("ix_file_lifecycle_outbox_due_lease", "state", "next_attempt_at", "locked_at"),
    )
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), default=uuid4, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    dedupe_key: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    file_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    project_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    state: Mapped[str] = mapped_column(
        String(16), server_default=text("'PENDING'"), nullable=False
    )
    attempt_count: Mapped[int] = mapped_column(Integer, server_default=text("0"), nullable=False)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    claim_token: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class FileStorageCleanup(Base):
    """Retryable storage compensation without a cascade to the source File."""

    __tablename__ = "file_storage_cleanup"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_file_storage_cleanup"),
        UniqueConstraint(
            "operation", "dedupe_key", name="uq_file_storage_cleanup_operation_dedupe"
        ),
        Index(
            "uq_file_storage_cleanup_operation_target",
            "operation",
            "object_key",
            text("COALESCE(multipart_id, '')"),
            unique=True,
        ),
        CheckConstraint(
            "operation IN ('ABORT_MULTIPART','DELETE_OBJECT','DISCOVER_MULTIPART')",
            name="ck_file_storage_cleanup_operation",
        ),
        CheckConstraint(
            "state IN ('PENDING','RUNNING','DONE')", name="ck_file_storage_cleanup_state"
        ),
        CheckConstraint("attempt_count >= 0", name="ck_file_storage_cleanup_attempt_count"),
        CheckConstraint(
            "dedupe_key ~ '^[0-9a-f]{64}$'", name="ck_file_storage_cleanup_dedupe_key"
        ),
        Index("ix_file_storage_cleanup_pending", "state", "next_attempt_at"),
        Index("ix_file_storage_cleanup_due_lease", "state", "next_attempt_at", "locked_at"),
    )
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), default=uuid4, nullable=False)
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(64), nullable=False)
    object_key: Mapped[str] = mapped_column(String(2048), nullable=False)
    multipart_id: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    lifecycle_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    state: Mapped[str] = mapped_column(
        String(16), server_default=text("'PENDING'"), nullable=False
    )
    attempt_count: Mapped[int] = mapped_column(Integer, server_default=text("0"), nullable=False)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    claim_token: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
