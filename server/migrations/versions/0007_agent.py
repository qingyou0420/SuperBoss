"""Add 霜月 agent tables.

Revision ID: 0007_agent
Revises: 0006_finance_entries
"""

from collections.abc import Sequence

from alembic import op

revision = "0007_agent"
down_revision = "0006_finance_entries"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_conversations (
            id UUID PRIMARY KEY,
            owner_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            title VARCHAR(80) NOT NULL DEFAULT '新对话',
            summary TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_message_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            archived_at TIMESTAMPTZ
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_messages (
            id UUID PRIMARY KEY,
            conversation_id UUID NOT NULL REFERENCES agent_conversations(id) ON DELETE CASCADE,
            role VARCHAR(16) NOT NULL,
            content TEXT NOT NULL DEFAULT '',
            tool_calls JSONB NOT NULL DEFAULT '{}'::jsonb,
            card_ids UUID[] NOT NULL DEFAULT '{}',
            token_usage JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_agent_messages_role CHECK (role IN ('user','assistant','tool','system'))
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_agent_messages_conversation "
        "ON agent_messages (conversation_id, created_at)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_cards (
            id UUID PRIMARY KEY,
            conversation_id UUID NOT NULL REFERENCES agent_conversations(id) ON DELETE CASCADE,
            message_id UUID REFERENCES agent_messages(id) ON DELETE SET NULL,
            kind VARCHAR(32) NOT NULL,
            payload JSONB NOT NULL,
            status VARCHAR(16) NOT NULL DEFAULT 'PROPOSED',
            decided_at TIMESTAMPTZ,
            committed_object_type VARCHAR(64),
            committed_object_id UUID,
            error TEXT,
            CONSTRAINT ck_agent_cards_status CHECK (
                status IN ('PROPOSED','CONFIRMED','COMMITTED','REVISED','REJECTED','FAILED')
            )
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_agent_cards_conversation ON agent_cards (conversation_id)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_soul_versions (
            id UUID PRIMARY KEY,
            content TEXT NOT NULL,
            note VARCHAR(255) NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            is_active BOOLEAN NOT NULL DEFAULT false
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_soul_active "
        "ON agent_soul_versions (is_active) WHERE is_active"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_memories (
            id UUID PRIMARY KEY,
            kind VARCHAR(32) NOT NULL,
            content TEXT NOT NULL,
            source_message_id UUID,
            importance SMALLINT NOT NULL DEFAULT 3,
            pinned BOOLEAN NOT NULL DEFAULT false,
            status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_recalled_at TIMESTAMPTZ,
            recall_count INTEGER NOT NULL DEFAULT 0,
            search tsvector GENERATED ALWAYS AS (to_tsvector('simple', coalesce(content, ''))) STORED,
            CONSTRAINT ck_agent_memories_kind CHECK (
                kind IN ('FACT','PREFERENCE','DECISION','PROJECT_NOTE','DAILY_DIGEST')
            ),
            CONSTRAINT ck_agent_memories_status CHECK (status IN ('ACTIVE','ARCHIVED')),
            CONSTRAINT ck_agent_memories_importance CHECK (importance BETWEEN 1 AND 5)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_agent_memories_status ON agent_memories (status)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS agent_memories")
    op.execute("DROP TABLE IF EXISTS agent_soul_versions")
    op.execute("DROP TABLE IF EXISTS agent_cards")
    op.execute("DROP TABLE IF EXISTS agent_messages")
    op.execute("DROP TABLE IF EXISTS agent_conversations")
