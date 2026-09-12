"""Placeholder settings and reward allocations.

Revision ID: 0015_placeholder_data
Revises: 0014_directory_project
"""

from collections.abc import Sequence

from alembic import op

revision = "0015_placeholder_data"
down_revision = "0014_directory_project"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS company_settings (
            key VARCHAR(64) PRIMARY KEY,
            value_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            is_placeholder BOOLEAN NOT NULL DEFAULT true
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS reward_allocations (
            id UUID PRIMARY KEY,
            project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            kind VARCHAR(16) NOT NULL,
            person_name VARCHAR(64) NOT NULL,
            share_cents BIGINT NOT NULL,
            is_placeholder BOOLEAN NOT NULL DEFAULT true,
            note TEXT NOT NULL DEFAULT '',
            CONSTRAINT ck_reward_allocations_kind CHECK (kind IN ('SURPLUS','POOL')),
            CONSTRAINT ck_reward_allocations_share CHECK (share_cents >= 0)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_reward_allocations_project "
        "ON reward_allocations (project_id, kind)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS reward_allocations")
    op.execute("DROP TABLE IF EXISTS company_settings")
