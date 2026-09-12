"""Ensure review-audit columns exist after an already-applied 0018/0019.

Revision ID: 0020_round6_integrity
Revises: 0019_round5_integrity
"""

from collections.abc import Sequence

from alembic import op

revision = "0020_round6_integrity"
down_revision = "0019_round5_integrity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE knowledge_revisions
            ADD COLUMN IF NOT EXISTS points_reviewed_by
            UUID REFERENCES users(id) ON DELETE SET NULL
        """
    )
    op.execute(
        "ALTER TABLE knowledge_revisions ADD COLUMN IF NOT EXISTS points_reviewed_at TIMESTAMPTZ"
    )
    op.execute(
        """
        ALTER TABLE knowledge_revision_pollution
            ADD COLUMN IF NOT EXISTS reviewed_by
            UUID REFERENCES users(id) ON DELETE SET NULL
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge_revision_review_events (
            id UUID PRIMARY KEY,
            revision_id UUID NOT NULL
                REFERENCES knowledge_revisions(id) ON DELETE CASCADE,
            doc_id UUID NOT NULL
                REFERENCES knowledge_docs(id) ON DELETE CASCADE,
            action VARCHAR(16) NOT NULL,
            outcome VARCHAR(32) NOT NULL,
            actor_id UUID REFERENCES users(id) ON DELETE SET NULL,
            previous_status VARCHAR(16) NOT NULL,
            new_status VARCHAR(16) NOT NULL,
            original_points_json JSONB NOT NULL DEFAULT '[]'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_knowledge_revision_review_events_revision
            ON knowledge_revision_review_events (revision_id, created_at)
        """
    )


def downgrade() -> None:
    return
