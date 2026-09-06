"""Message seq, conversation summary watermark, drop is_test.

Revision ID: 0009_message_seq
Revises: 0008_knowledge
"""

from collections.abc import Sequence

from alembic import op

revision = "0009_message_seq"
down_revision = "0008_knowledge"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE agent_messages ADD COLUMN IF NOT EXISTS seq BIGSERIAL NOT NULL")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_agent_messages_conversation_seq "
        "ON agent_messages (conversation_id, seq)"
    )
    op.execute(
        "ALTER TABLE agent_conversations ADD COLUMN IF NOT EXISTS summarized_until BIGINT"
    )
    op.execute("ALTER TABLE projects DROP COLUMN IF EXISTS is_test")


def downgrade() -> None:
    op.execute(
        "ALTER TABLE projects ADD COLUMN IF NOT EXISTS is_test BOOLEAN NOT NULL DEFAULT false"
    )
    op.execute("ALTER TABLE agent_conversations DROP COLUMN IF EXISTS summarized_until")
    op.execute("DROP INDEX IF EXISTS ix_agent_messages_conversation_seq")
    op.execute("ALTER TABLE agent_messages DROP COLUMN IF EXISTS seq")
