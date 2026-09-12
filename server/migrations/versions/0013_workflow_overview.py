"""Project nodes, schedule changes, knowledge links, finance extras.

Revision ID: 0013_workflow_overview
Revises: 0012_directory_fees
"""

from collections.abc import Sequence

from alembic import op

revision = "0013_workflow_overview"
down_revision = "0012_directory_fees"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS project_nodes (
            id UUID PRIMARY KEY,
            project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            sort_order INTEGER NOT NULL DEFAULT 0,
            title VARCHAR(255) NOT NULL,
            planned_start DATE,
            planned_end DATE,
            status VARCHAR(16) NOT NULL DEFAULT 'OPEN',
            completed_at TIMESTAMPTZ,
            completed_by UUID REFERENCES users(id) ON DELETE SET NULL,
            preparation JSONB NOT NULL DEFAULT '[]'::jsonb,
            document_name VARCHAR(255) NOT NULL DEFAULT '',
            photo_required BOOLEAN NOT NULL DEFAULT false,
            evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
            CONSTRAINT ck_project_nodes_status CHECK (status IN ('OPEN','DONE')),
            CONSTRAINT ck_project_nodes_title CHECK (char_length(title) BETWEEN 1 AND 255)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_project_nodes_project "
        "ON project_nodes (project_id, sort_order)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS project_schedule_changes (
            id UUID PRIMARY KEY,
            project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            node_id UUID REFERENCES project_nodes(id) ON DELETE SET NULL,
            days INTEGER NOT NULL,
            reason TEXT NOT NULL DEFAULT '',
            before_json JSONB NOT NULL DEFAULT '[]'::jsonb,
            after_json JSONB NOT NULL DEFAULT '[]'::jsonb,
            created_by UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "ALTER TABLE knowledge_docs ADD COLUMN IF NOT EXISTS project_id UUID "
        "REFERENCES projects(id) ON DELETE SET NULL"
    )
    op.execute(
        "ALTER TABLE knowledge_docs ADD COLUMN IF NOT EXISTS stage_title VARCHAR(255) "
        "NOT NULL DEFAULT ''"
    )
    op.execute(
        "ALTER TABLE knowledge_docs ADD COLUMN IF NOT EXISTS change_reason TEXT NOT NULL DEFAULT ''"
    )
    op.execute(
        "ALTER TABLE knowledge_docs ADD COLUMN IF NOT EXISTS is_canonical BOOLEAN "
        "NOT NULL DEFAULT false"
    )
    op.execute(
        "ALTER TABLE finance_entries ADD COLUMN IF NOT EXISTS batch_key VARCHAR(64) "
        "NOT NULL DEFAULT ''"
    )
    op.execute("ALTER TABLE finance_entries ADD COLUMN IF NOT EXISTS paid_on DATE")
    op.execute("ALTER TABLE finance_entries ADD COLUMN IF NOT EXISTS paid_cents BIGINT")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS company_month_costs (
            month CHAR(7) PRIMARY KEY,
            amount_cents BIGINT NOT NULL,
            CONSTRAINT ck_company_month_costs_amount CHECK (amount_cents >= 0)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS company_month_costs")
    op.execute("ALTER TABLE finance_entries DROP COLUMN IF EXISTS paid_cents")
    op.execute("ALTER TABLE finance_entries DROP COLUMN IF EXISTS paid_on")
    op.execute("ALTER TABLE finance_entries DROP COLUMN IF EXISTS batch_key")
    op.execute("ALTER TABLE knowledge_docs DROP COLUMN IF EXISTS is_canonical")
    op.execute("ALTER TABLE knowledge_docs DROP COLUMN IF EXISTS change_reason")
    op.execute("ALTER TABLE knowledge_docs DROP COLUMN IF EXISTS stage_title")
    op.execute("ALTER TABLE knowledge_docs DROP COLUMN IF EXISTS project_id")
    op.execute("DROP TABLE IF EXISTS project_schedule_changes")
    op.execute("DROP TABLE IF EXISTS project_nodes")
