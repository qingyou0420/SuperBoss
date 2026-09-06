"""Drop project_members. Membership is not an access-control surface.

Revision ID: 0010_drop_members
Revises: 0009_message_seq
"""

from collections.abc import Sequence

from alembic import op

revision = "0010_drop_members"
down_revision = "0009_message_seq"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS project_members")


def downgrade() -> None:
    op.execute(
        "CREATE TABLE IF NOT EXISTS project_members ("
        "project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE, "
        "user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE, "
        "created_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
        "PRIMARY KEY (project_id, user_id))"
    )
