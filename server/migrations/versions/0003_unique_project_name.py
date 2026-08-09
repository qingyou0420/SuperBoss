"""Require unique project names.

Revision ID: 0003_unique_project_name
Revises: 0002_auth_sessions
Create Date: 2026-08-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_unique_project_name"
down_revision: str | None = "0002_auth_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("uq_projects_name", "projects", type_="unique")
    op.create_check_constraint("ck_projects_name_trimmed", "projects", "name = btrim(name)")
    op.create_check_constraint(
        "ck_projects_name_length", "projects", "char_length(name) BETWEEN 1 AND 255"
    )
    op.create_index("uq_projects_name_ci", "projects", [sa.text("lower(name)")], unique=True)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_projects_name_ci")
    op.execute("ALTER TABLE projects DROP CONSTRAINT IF EXISTS ck_projects_name_length")
    op.execute("ALTER TABLE projects DROP CONSTRAINT IF EXISTS ck_projects_name_trimmed")
    op.execute("ALTER TABLE projects DROP CONSTRAINT IF EXISTS uq_projects_name")
    op.create_unique_constraint("uq_projects_name", "projects", ["name"])
