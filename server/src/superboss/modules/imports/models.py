"""Normalized persistence for device-submitted K3 result packages."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from superboss.core.db import Base


class ImportStatus(StrEnum):
    UPLOADING = "UPLOADING"
    SCANNING = "SCANNING"
    RECEIVED = "RECEIVED"
    REJECTED = "REJECTED"
    CONFLICT = "CONFLICT"


class AttachmentKind(StrEnum):
    ORIGINAL = "ORIGINAL"
    REVISED = "REVISED"
    K3_RAW = "K3_RAW"


class ImportJob(Base):
    __tablename__ = "import_jobs"
    __table_args__ = (
        UniqueConstraint("id", "project_id", name="uq_import_jobs_id_project"),
        UniqueConstraint(
            "device_id",
            "idempotency_key",
            name="uq_import_jobs_device_idempotency",
        ),
        CheckConstraint(
            "status IN ('UPLOADING','SCANNING','RECEIVED','REJECTED','CONFLICT')",
            name="ck_import_jobs_status",
        ),
        CheckConstraint(
            "octet_length(canonical_manifest_json::text) <= 65536",
            name="ck_import_jobs_manifest_size",
        ),
        CheckConstraint(
            "jsonb_typeof(canonical_manifest_json) = 'object'",
            name="ck_import_jobs_manifest_object",
        ),
        CheckConstraint(
            "manifest_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_import_jobs_manifest_fingerprint",
        ),
        CheckConstraint(
            "base_sha256 IS NULL OR base_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_import_jobs_base_sha256",
        ),
        CheckConstraint(
            "char_length(idempotency_key) > 0",
            name="ck_import_jobs_idempotency_key",
        ),
        CheckConstraint(
            "char_length(local_task_id) > 0",
            name="ck_import_jobs_local_task_id",
        ),
        CheckConstraint(
            "external_document_reference IS NULL "
            "OR char_length(external_document_reference) > 0",
            name="ck_import_jobs_external_reference",
        ),
        CheckConstraint(
            "result_code IS NULL OR (char_length(result_code) BETWEEN 1 AND 64 "
            "AND result_code !~ '[[:cntrl:]]')",
            name="ck_import_jobs_result_code",
        ),
        CheckConstraint(
            "(status = 'UPLOADING' AND submitted_at IS NULL) OR "
            "(status IN ('SCANNING','RECEIVED','REJECTED','CONFLICT') "
            "AND submitted_at IS NOT NULL)",
            name="ck_import_jobs_submission_state",
        ),
        CheckConstraint(
            "(status IN ('UPLOADING','SCANNING','RECEIVED') AND result_code IS NULL) OR "
            "(status IN ('REJECTED','CONFLICT') AND result_code IS NOT NULL)",
            name="ck_import_jobs_result_state",
        ),
        CheckConstraint(
            "updated_at >= created_at "
            "AND (submitted_at IS NULL OR "
            "(submitted_at >= created_at AND submitted_at <= updated_at))",
            name="ck_import_jobs_time_order",
        ),
        Index("ix_import_jobs_project_created", "project_id", "created_at"),
        Index("ix_import_jobs_device_created", "device_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    device_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("device_connections.id", ondelete="RESTRICT"),
        nullable=False,
    )
    project_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="RESTRICT"),
        nullable=False,
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    local_task_id: Mapped[str] = mapped_column(String(255), nullable=False)
    external_document_reference: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    base_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    canonical_manifest_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    manifest_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[ImportStatus] = mapped_column(
        Enum(ImportStatus, name="import_status", native_enum=False),
        server_default=text("'UPLOADING'"),
        nullable=False,
    )
    result_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ImportAttachment(Base):
    __tablename__ = "import_attachments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["job_id", "project_id"],
            ["import_jobs.id", "import_jobs.project_id"],
            name="fk_import_attachments_job_project",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["file_id", "project_id"],
            ["files.id", "files.project_id"],
            name="fk_import_attachments_file_project",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["upload_id", "file_id", "project_id"],
            ["uploads.id", "uploads.file_id", "uploads.project_id"],
            name="fk_import_attachments_upload_file_project",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("job_id", "kind", name="uq_import_attachments_job_kind"),
        UniqueConstraint("file_id", name="uq_import_attachments_file"),
        UniqueConstraint("upload_id", name="uq_import_attachments_upload"),
        CheckConstraint(
            "kind IN ('ORIGINAL','REVISED','K3_RAW')",
            name="ck_import_attachments_kind",
        ),
        Index("ix_import_attachments_project", "project_id"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    job_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    project_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    file_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    upload_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    kind: Mapped[AttachmentKind] = mapped_column(
        Enum(AttachmentKind, name="import_attachment_kind", native_enum=False),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
