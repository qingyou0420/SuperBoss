"""Require unique project names.

Revision ID: 0003_unique_project_name
Revises: 0002_auth_sessions
Create Date: 2026-08-09
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003_unique_project_name"
down_revision: str | None = "0002_auth_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint("uq_projects_name", "projects", ["name"])


def downgrade() -> None:
    op.drop_constraint("uq_projects_name", "projects", type_="unique")
