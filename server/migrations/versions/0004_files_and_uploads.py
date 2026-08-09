"""Add resumable file uploads.

Revision ID: 0004_files_and_uploads
Revises: 0003_unique_project_name
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "0004_files_and_uploads"
down_revision = "0003_unique_project_name"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "files",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "project_id",
            sa.UUID(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("filename", sa.String(1024), nullable=False),
        sa.Column("category", sa.String(255), nullable=False),
        sa.Column("file_date", sa.Date(), nullable=False),
        sa.Column("object_key", sa.String(2048), nullable=False, unique=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column(
            "state",
            sa.Enum(
                "UPLOADING", "QUARANTINED", "SCANNING", "CLEAN", "INFECTED", "FAILED",
                name="file_state", native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("uploader_kind", sa.String(16), nullable=False, server_default="user"),
        sa.Column("uploader_id", sa.UUID(), nullable=False),
        sa.Column("content_type", sa.String(255), nullable=False),
        sa.Column("scan_result", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("state IN ('UPLOADING','QUARANTINED','SCANNING','CLEAN','INFECTED','FAILED')", name="ck_files_state"),
        sa.CheckConstraint("size_bytes BETWEEN 1 AND 104857600", name="ck_files_size"),
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="ck_files_sha256"),
        sa.CheckConstraint("uploader_kind IN ('user','device','system')", name="ck_files_uploader_kind"),
        sa.CheckConstraint("char_length(filename) > 0", name="ck_files_filename"),
        sa.CheckConstraint("char_length(category) > 0", name="ck_files_category"),
        sa.CheckConstraint("char_length(object_key) > 0", name="ck_files_object_key"),
        sa.CheckConstraint("char_length(content_type) > 0", name="ck_files_content_type"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_table(
        "uploads",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "file_id",
            sa.UUID(),
            sa.ForeignKey("files.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("project_id", sa.UUID(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("uploader_kind", sa.String(16), nullable=False),
        sa.Column("uploader_id", sa.UUID(), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("metadata_fingerprint", sa.String(64), nullable=False),
        sa.Column("multipart_id", sa.String(1024), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "project_id", "uploader_kind", "uploader_id", "idempotency_key", name="uq_upload_idempotency"
        ),
        sa.CheckConstraint("uploader_kind IN ('user','device','system')", name="ck_uploads_uploader_kind"),
        sa.CheckConstraint("char_length(idempotency_key) > 0", name="ck_uploads_idempotency_key"),
        sa.CheckConstraint("metadata_fingerprint ~ '^[0-9a-f]{64}$'", name="ck_uploads_fingerprint"),
    )


def downgrade() -> None:
    op.drop_table("uploads")
    op.drop_table("files")
