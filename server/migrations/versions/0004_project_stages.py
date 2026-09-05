"""Add project stage, progress, dates, and milestones.

Revision ID: 0004_project_stages
Revises: 0003_add_manager_role
"""

from collections.abc import Sequence

from alembic import op

revision = "0004_project_stages"
down_revision = "0003_add_manager_role"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE projects ADD COLUMN IF NOT EXISTS description TEXT NOT NULL DEFAULT ''")
    op.execute(
        "ALTER TABLE projects ADD COLUMN IF NOT EXISTS stage VARCHAR(16) NOT NULL DEFAULT 'PLANNING'"
    )
    op.execute(
        "ALTER TABLE projects ADD COLUMN IF NOT EXISTS progress_percent SMALLINT NOT NULL DEFAULT 0"
    )
    op.execute("ALTER TABLE projects ADD COLUMN IF NOT EXISTS starts_on DATE")
    op.execute("ALTER TABLE projects ADD COLUMN IF NOT EXISTS due_on DATE")
    op.execute("ALTER TABLE projects DROP CONSTRAINT IF EXISTS ck_projects_stage")
    op.execute(
        "ALTER TABLE projects ADD CONSTRAINT ck_projects_stage "
        "CHECK (stage IN ('PLANNING','ACTIVE','DELIVERING','REVIEW','ARCHIVED'))"
    )
    op.execute("ALTER TABLE projects DROP CONSTRAINT IF EXISTS ck_projects_progress")
    op.execute(
        "ALTER TABLE projects ADD CONSTRAINT ck_projects_progress "
        "CHECK (progress_percent BETWEEN 0 AND 100)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS project_milestones (
            id UUID PRIMARY KEY,
            project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            title VARCHAR(255) NOT NULL,
            due_on DATE,
            done_at TIMESTAMPTZ,
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_project_milestones_title_length CHECK (char_length(title) BETWEEN 1 AND 255)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_project_milestones_project_sort "
        "ON project_milestones (project_id, sort_order)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS project_milestones")
    op.execute("ALTER TABLE projects DROP CONSTRAINT IF EXISTS ck_projects_stage")
    op.execute("ALTER TABLE projects DROP CONSTRAINT IF EXISTS ck_projects_progress")
    op.execute("ALTER TABLE projects DROP COLUMN IF EXISTS due_on")
    op.execute("ALTER TABLE projects DROP COLUMN IF EXISTS starts_on")
    op.execute("ALTER TABLE projects DROP COLUMN IF EXISTS progress_percent")
    op.execute("ALTER TABLE projects DROP COLUMN IF EXISTS stage")
    op.execute("ALTER TABLE projects DROP COLUMN IF EXISTS description")
