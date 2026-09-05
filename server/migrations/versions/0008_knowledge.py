"""Add knowledge docs and points.

Revision ID: 0008_knowledge
Revises: 0007_agent
"""

from collections.abc import Sequence

from alembic import op

revision = "0008_knowledge"
down_revision = "0007_agent"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge_docs (
            id UUID PRIMARY KEY,
            title VARCHAR(255) NOT NULL,
            body_md TEXT NOT NULL DEFAULT '',
            tags VARCHAR(64)[] NOT NULL DEFAULT '{}',
            status VARCHAR(16) NOT NULL DEFAULT 'DRAFT',
            source_file_id UUID,
            created_by UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            search tsvector GENERATED ALWAYS AS (
                to_tsvector('simple', coalesce(title,'') || ' ' || coalesce(body_md,''))
            ) STORED,
            CONSTRAINT ck_knowledge_docs_status CHECK (status IN ('DRAFT','PUBLISHED'))
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge_points (
            id UUID PRIMARY KEY,
            doc_id UUID NOT NULL REFERENCES knowledge_docs(id) ON DELETE CASCADE,
            title VARCHAR(255) NOT NULL,
            body_md TEXT NOT NULL DEFAULT '',
            source_file_id UUID,
            sort_order INTEGER NOT NULL DEFAULT 0
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS knowledge_points")
    op.execute("DROP TABLE IF EXISTS knowledge_docs")
