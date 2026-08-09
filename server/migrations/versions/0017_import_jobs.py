"""Add normalized K3 import jobs and attachments.

Revision ID: 0017_import_jobs
Revises: 0016_device_connections
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0017_import_jobs"
down_revision = "0016_device_connections"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_uploads_id_file_project",
        "uploads",
        ["id", "file_id", "project_id"],
    )
    op.create_table(
        "import_idempotency_claims",
        sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("manifest_fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "idempotency_key ~ '^[!-~]{1,255}$'",
            name="ck_import_idempotency_claims_key",
        ),
        sa.CheckConstraint(
            "manifest_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_import_idempotency_claims_fingerprint",
        ),
        sa.ForeignKeyConstraint(
            ["device_id"],
            ["device_connections.id"],
            name="fk_import_idempotency_claims_device",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "device_id",
            "idempotency_key",
            name="pk_import_idempotency_claims",
        ),
    )
    op.create_table(
        "import_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("local_task_id", sa.String(length=255), nullable=False),
        sa.Column("external_document_reference", sa.String(length=1024), nullable=True),
        sa.Column("base_sha256", sa.String(length=64), nullable=True),
        sa.Column("canonical_manifest_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("manifest_fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "UPLOADING",
                "SCANNING",
                "RECEIVED",
                "REJECTED",
                "CONFLICT",
                name="import_status",
                native_enum=False,
            ),
            server_default=sa.text("'UPLOADING'"),
            nullable=False,
        ),
        sa.Column("result_code", sa.String(length=64), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('UPLOADING','SCANNING','RECEIVED','REJECTED','CONFLICT')",
            name="ck_import_jobs_status",
        ),
        sa.CheckConstraint(
            "octet_length(canonical_manifest_json::text) <= 65536",
            name="ck_import_jobs_manifest_size",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(canonical_manifest_json) = 'object'",
            name="ck_import_jobs_manifest_object",
        ),
        sa.CheckConstraint(
            "manifest_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_import_jobs_manifest_fingerprint",
        ),
        sa.CheckConstraint(
            "base_sha256 IS NULL OR base_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_import_jobs_base_sha256",
        ),
        sa.CheckConstraint(
            "char_length(idempotency_key) > 0",
            name="ck_import_jobs_idempotency_key",
        ),
        sa.CheckConstraint(
            "char_length(local_task_id) > 0",
            name="ck_import_jobs_local_task_id",
        ),
        sa.CheckConstraint(
            "external_document_reference IS NULL "
            "OR char_length(external_document_reference) > 0",
            name="ck_import_jobs_external_reference",
        ),
        sa.CheckConstraint(
            "result_code IS NULL OR result_code ~ '^[A-Z][A-Z0-9_]{0,63}$'",
            name="ck_import_jobs_result_code",
        ),
        sa.CheckConstraint(
            "(status = 'UPLOADING' AND submitted_at IS NULL) OR "
            "(status IN ('SCANNING','RECEIVED','REJECTED','CONFLICT') "
            "AND submitted_at IS NOT NULL)",
            name="ck_import_jobs_submission_state",
        ),
        sa.CheckConstraint(
            "(status IN ('UPLOADING','SCANNING','RECEIVED') AND result_code IS NULL) OR "
            "(status IN ('REJECTED','CONFLICT') AND result_code IS NOT NULL)",
            name="ck_import_jobs_result_state",
        ),
        sa.CheckConstraint(
            "updated_at >= created_at "
            "AND (submitted_at IS NULL OR "
            "(submitted_at >= created_at AND submitted_at <= updated_at))",
            name="ck_import_jobs_time_order",
        ),
        sa.ForeignKeyConstraint(
            ["device_id"],
            ["device_connections.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "device_id",
            "idempotency_key",
            name="uq_import_jobs_device_idempotency",
        ),
        sa.UniqueConstraint("id", "project_id", name="uq_import_jobs_id_project"),
    )
    op.create_index(
        "ix_import_jobs_project_created",
        "import_jobs",
        ["project_id", "created_at"],
    )
    op.create_index(
        "ix_import_jobs_device_created",
        "import_jobs",
        ["device_id", "created_at"],
    )
    op.create_table(
        "import_attachments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("file_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("upload_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "ORIGINAL",
                "REVISED",
                "K3_RAW",
                name="import_attachment_kind",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('ORIGINAL','REVISED','K3_RAW')",
            name="ck_import_attachments_kind",
        ),
        sa.ForeignKeyConstraint(
            ["job_id", "project_id"],
            ["import_jobs.id", "import_jobs.project_id"],
            name="fk_import_attachments_job_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["file_id", "project_id"],
            ["files.id", "files.project_id"],
            name="fk_import_attachments_file_project",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["upload_id", "file_id", "project_id"],
            ["uploads.id", "uploads.file_id", "uploads.project_id"],
            name="fk_import_attachments_upload_file_project",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("file_id", name="uq_import_attachments_file"),
        sa.UniqueConstraint("job_id", "kind", name="uq_import_attachments_job_kind"),
        sa.UniqueConstraint("upload_id", name="uq_import_attachments_upload"),
    )
    op.create_index(
        "ix_import_attachments_project",
        "import_attachments",
        ["project_id"],
    )


def _guard_downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM import_idempotency_claims)
                OR EXISTS (SELECT 1 FROM import_attachments)
                OR EXISTS (SELECT 1 FROM import_jobs)
            THEN
                RAISE EXCEPTION USING
                    MESSAGE = 'SUPERBOSS_IMPORT_DOWNGRADE_BLOCKED',
                    DETAIL = 'Import idempotency, job, or attachment state exists.',
                    HINT = 'Purge import state through an approved maintenance flow.';
            END IF;
        END;
        $$;
        """
    )


def downgrade() -> None:
    _guard_downgrade()
    op.drop_index("ix_import_attachments_project", table_name="import_attachments")
    op.drop_table("import_attachments")
    op.drop_index("ix_import_jobs_device_created", table_name="import_jobs")
    op.drop_index("ix_import_jobs_project_created", table_name="import_jobs")
    op.drop_table("import_jobs")
    op.drop_table("import_idempotency_claims")
    op.drop_constraint("uq_uploads_id_file_project", "uploads", type_="unique")
