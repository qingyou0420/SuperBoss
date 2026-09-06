"""Backfill agent_messages.seq by created_at, id.

Revision ID: 0011_backfill_seq
Revises: 0010_drop_members
"""

from collections.abc import Sequence

from alembic import op

revision = "0011_backfill_seq"
down_revision = "0010_drop_members"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "UPDATE agent_messages AS message SET seq = ranked.rn "
        "FROM (SELECT id, row_number() OVER "
        "(PARTITION BY conversation_id ORDER BY created_at, id) AS rn "
        "FROM agent_messages) AS ranked "
        "WHERE message.id = ranked.id"
    )
    op.execute(
        "SELECT setval("
        "pg_get_serial_sequence('agent_messages', 'seq'), "
        "COALESCE((SELECT MAX(seq) FROM agent_messages), 1), true)"
    )


def downgrade() -> None:
    return
