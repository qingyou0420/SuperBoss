"""Add durable file lifecycle recovery persistence.

Revision ID: 0006_file_lifecycle_recovery
Revises: 0005_file_lifecycle
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006_file_lifecycle_recovery"
down_revision = "0005_file_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_uploads_multipart_id_not_empty", "uploads", type_="check")
    op.alter_column("uploads", "multipart_id", existing_type=sa.String(length=1024), nullable=True)
    op.create_check_constraint(
        "ck_uploads_multipart_id_empty_or_nonempty",
        "uploads",
        "multipart_id IS NULL OR char_length(multipart_id) > 0",
    )

    op.create_table(
        "file_upload_lifecycle",
        sa.Column("upload_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("file_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("object_key", sa.String(length=2048), nullable=False),
        sa.Column("multipart_id", sa.String(length=1024), nullable=True),
        sa.Column("content_type", sa.String(length=255), nullable=False),
        sa.Column("declared_size_bytes", sa.Integer(), nullable=False),
        sa.Column("provision_state", sa.String(length=32), server_default="READY", nullable=False),
        sa.Column("completion_state", sa.String(length=32), server_default="NONE", nullable=False),
        sa.Column("parts_digest", sa.String(length=64), nullable=True),
        sa.Column("completion_event_key", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "provision_state IN ('PROVISIONING','READY','CANCEL_REQUESTED','TERMINAL')",
            name="ck_file_upload_lifecycle_provision_state",
        ),
        sa.CheckConstraint(
            "completion_state IN ('NONE','PREPARED','VERIFIED','QUARANTINED','COMPENSATION_PENDING')",
            name="ck_file_upload_lifecycle_completion_state",
        ),
        sa.CheckConstraint("declared_size_bytes BETWEEN 1 AND 104857600", name="ck_file_upload_lifecycle_size"),
        sa.CheckConstraint("char_length(object_key) > 0", name="ck_file_upload_lifecycle_object_key"),
        sa.CheckConstraint("char_length(content_type) > 0", name="ck_file_upload_lifecycle_content_type"),
        sa.CheckConstraint(
            "multipart_id IS NULL OR char_length(multipart_id) > 0",
            name="ck_file_upload_lifecycle_multipart_id",
        ),
        sa.CheckConstraint(
            "parts_digest IS NULL OR parts_digest ~ '^[0-9a-f]{64}$'",
            name="ck_file_upload_lifecycle_parts_digest",
        ),
        sa.PrimaryKeyConstraint("upload_id", name="pk_file_upload_lifecycle"),
        sa.UniqueConstraint("file_id", name="uq_file_upload_lifecycle_file_id"),
        sa.UniqueConstraint("completion_event_key", name="uq_file_upload_lifecycle_completion_event"),
    )
    op.create_index(
        "ix_file_upload_lifecycle_nonterminal",
        "file_upload_lifecycle",
        ["provision_state", "completion_state"],
    )
    op.create_table(
        "file_lifecycle_outbox",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("dedupe_key", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("file_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("state", sa.String(length=16), server_default="PENDING", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("kind IN ('scan_dispatch','completion_audit')", name="ck_file_lifecycle_outbox_kind"),
        sa.CheckConstraint("state IN ('PENDING','DELIVERING','DELIVERED')", name="ck_file_lifecycle_outbox_state"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_file_lifecycle_outbox_attempt_count"),
        sa.PrimaryKeyConstraint("id", name="pk_file_lifecycle_outbox"),
        sa.UniqueConstraint("kind", "dedupe_key", name="uq_file_lifecycle_outbox_kind_dedupe"),
    )
    op.create_index("ix_file_lifecycle_outbox_pending", "file_lifecycle_outbox", ["state", "next_attempt_at"])
    op.create_table(
        "file_storage_cleanup",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("operation", sa.String(length=32), nullable=False),
        sa.Column("dedupe_key", sa.String(length=64), nullable=False),
        sa.Column("object_key", sa.String(length=2048), nullable=False),
        sa.Column("multipart_id", sa.String(length=1024), nullable=True),
        sa.Column("lifecycle_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("state", sa.String(length=16), server_default="PENDING", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("operation IN ('ABORT_MULTIPART','DELETE_OBJECT')", name="ck_file_storage_cleanup_operation"),
        sa.CheckConstraint("state IN ('PENDING','RUNNING','DONE')", name="ck_file_storage_cleanup_state"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_file_storage_cleanup_attempt_count"),
        sa.CheckConstraint("dedupe_key ~ '^[0-9a-f]{64}$'", name="ck_file_storage_cleanup_dedupe_key"),
        sa.PrimaryKeyConstraint("id", name="pk_file_storage_cleanup"),
        sa.UniqueConstraint("operation", "dedupe_key", name="uq_file_storage_cleanup_operation_dedupe"),
    )
    op.create_index("ix_file_storage_cleanup_pending", "file_storage_cleanup", ["state", "next_attempt_at"])

    op.add_column("audit_logs", sa.Column("event_key", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_index(
        "uq_audit_logs_event_key",
        "audit_logs",
        ["event_key"],
        unique=True,
        postgresql_where=sa.text("event_key IS NOT NULL"),
    )

    op.execute(
        "INSERT INTO file_upload_lifecycle ("
        "upload_id, file_id, project_id, object_key, multipart_id, content_type, declared_size_bytes, "
        "provision_state, completion_state"
        ") "
        "SELECT uploads.id, uploads.file_id, uploads.project_id, files.object_key, uploads.multipart_id, "
        "files.content_type, files.size_bytes, 'READY', 'NONE' "
        "FROM uploads JOIN files ON files.id = uploads.file_id"
    )


def downgrade() -> None:
    op.drop_index("uq_audit_logs_event_key", table_name="audit_logs")
    op.drop_column("audit_logs", "event_key")
    op.drop_index("ix_file_storage_cleanup_pending", table_name="file_storage_cleanup")
    op.drop_table("file_storage_cleanup")
    op.drop_index("ix_file_lifecycle_outbox_pending", table_name="file_lifecycle_outbox")
    op.drop_table("file_lifecycle_outbox")
    op.drop_index("ix_file_upload_lifecycle_nonterminal", table_name="file_upload_lifecycle")
    op.drop_table("file_upload_lifecycle")
    op.drop_constraint("ck_uploads_multipart_id_empty_or_nonempty", "uploads", type_="check")
    op.alter_column("uploads", "multipart_id", existing_type=sa.String(length=1024), nullable=False)
    op.create_check_constraint(
        "ck_uploads_multipart_id_not_empty", "uploads", "char_length(multipart_id) > 0"
    )
