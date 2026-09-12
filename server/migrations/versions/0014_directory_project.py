"""Directory-to-project link.

Revision ID: 0014_directory_project
Revises: 0013_workflow_overview
"""

from collections.abc import Sequence

from alembic import op

revision = "0014_directory_project"
down_revision = "0013_workflow_overview"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE directory_entries ADD COLUMN IF NOT EXISTS project_id UUID "
        "REFERENCES projects(id) ON DELETE SET NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_directory_entries_project ON directory_entries (project_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_directory_entries_project")
    op.execute("ALTER TABLE directory_entries DROP COLUMN IF EXISTS project_id")
