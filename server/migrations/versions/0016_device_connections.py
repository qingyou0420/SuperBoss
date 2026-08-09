"""Add least-privilege Kimi device credentials.

Revision ID: 0016_device_connections
Revises: 0015_discover_unbound_multipart
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0016_device_connections"
down_revision = "0015_discover_unbound_multipart"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "device_pairing_codes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "code_hash ~ '^[0-9a-f]{64}$'", name="ck_device_pairing_codes_hash"
        ),
        sa.CheckConstraint(
            "expires_at > created_at", name="ck_device_pairing_codes_expiry_order"
        ),
        sa.CheckConstraint(
            "used_at IS NULL OR (used_at >= created_at AND used_at <= expires_at)",
            name="ck_device_pairing_codes_used_order",
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code_hash"),
    )
    op.create_index(
        "ix_device_pairing_codes_owner_created",
        "device_pairing_codes",
        ["owner_id", "created_at"],
    )
    op.create_index(
        "ix_device_pairing_codes_expires_at", "device_pairing_codes", ["expires_at"]
    )
    op.create_table(
        "device_pairing_projects",
        sa.Column("pairing_code_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["pairing_code_id"], ["device_pairing_codes.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("pairing_code_id", "project_id"),
    )
    op.create_index(
        "ix_device_pairing_projects_project", "device_pairing_projects", ["project_id"]
    )
    op.create_table(
        "device_connections",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column(
            "paired_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "name = btrim(name, E' \\t\\r\\n' || chr(160))",
            name="ck_device_connections_name_trimmed",
        ),
        sa.CheckConstraint(
            "char_length(name) BETWEEN 1 AND 128", name="ck_device_connections_name_length"
        ),
        sa.CheckConstraint(
            "last_used_at IS NULL OR last_used_at >= paired_at",
            name="ck_device_connections_last_used_order",
        ),
        sa.CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= paired_at",
            name="ck_device_connections_revoked_order",
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_device_connections_owner_paired", "device_connections", ["owner_id", "paired_at"]
    )
    op.create_table(
        "device_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("access_jti", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("refresh_token_hash", sa.String(length=64), nullable=False),
        sa.Column("access_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("refresh_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("refresh_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "refresh_token_hash ~ '^[0-9a-f]{64}$'", name="ck_device_sessions_refresh_hash"
        ),
        sa.CheckConstraint(
            "access_expires_at > created_at", name="ck_device_sessions_access_expiry_order"
        ),
        sa.CheckConstraint(
            "refresh_expires_at > access_expires_at",
            name="ck_device_sessions_refresh_expiry_order",
        ),
        sa.CheckConstraint(
            "refresh_used_at IS NULL OR refresh_used_at >= created_at",
            name="ck_device_sessions_refresh_used_order",
        ),
        sa.CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= created_at",
            name="ck_device_sessions_revoked_order",
        ),
        sa.ForeignKeyConstraint(["device_id"], ["device_connections.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("access_jti"),
        sa.UniqueConstraint("refresh_token_hash"),
    )
    op.create_index(
        "ix_device_sessions_device_created", "device_sessions", ["device_id", "created_at"]
    )
    op.create_table(
        "device_project_grants",
        sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["device_id"], ["device_connections.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("device_id", "project_id"),
    )
    op.create_index(
        "ix_device_project_grants_project", "device_project_grants", ["project_id"]
    )
    op.create_table(
        "device_scope_grants",
        sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scope", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "scope IN ('imports:create','imports:read-own','imports:submit','imports:upload')",
            name="ck_device_scope_grants_scope",
        ),
        sa.ForeignKeyConstraint(["device_id"], ["device_connections.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("device_id", "scope"),
    )


def _guard_downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM device_pairing_codes)
                OR EXISTS (SELECT 1 FROM device_pairing_projects)
                OR EXISTS (SELECT 1 FROM device_connections)
                OR EXISTS (SELECT 1 FROM device_sessions)
                OR EXISTS (SELECT 1 FROM device_project_grants)
                OR EXISTS (SELECT 1 FROM device_scope_grants)
            THEN
                RAISE EXCEPTION USING
                    MESSAGE = 'SUPERBOSS_DEVICE_DOWNGRADE_BLOCKED',
                    DETAIL = 'Device credential or grant state exists.',
                    HINT = 'Revoke and purge device state through an approved maintenance flow.';
            END IF;
        END;
        $$;
        """
    )


def downgrade() -> None:
    _guard_downgrade()
    op.drop_table("device_scope_grants")
    op.drop_index("ix_device_project_grants_project", table_name="device_project_grants")
    op.drop_table("device_project_grants")
    op.drop_index("ix_device_sessions_device_created", table_name="device_sessions")
    op.drop_table("device_sessions")
    op.drop_index("ix_device_connections_owner_paired", table_name="device_connections")
    op.drop_table("device_connections")
    op.drop_index("ix_device_pairing_projects_project", table_name="device_pairing_projects")
    op.drop_table("device_pairing_projects")
    op.drop_index("ix_device_pairing_codes_expires_at", table_name="device_pairing_codes")
    op.drop_index("ix_device_pairing_codes_owner_created", table_name="device_pairing_codes")
    op.drop_table("device_pairing_codes")

