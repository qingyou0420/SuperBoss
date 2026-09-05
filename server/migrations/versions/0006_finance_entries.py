"""Add finance entries and adjustments.

Revision ID: 0006_finance_entries
Revises: 0005_drive_folders
"""

from collections.abc import Sequence

from alembic import op

revision = "0006_finance_entries"
down_revision = "0005_drive_folders"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS finance_entries (
            id UUID PRIMARY KEY,
            kind VARCHAR(16) NOT NULL,
            scope VARCHAR(16) NOT NULL,
            project_id UUID REFERENCES projects(id) ON DELETE RESTRICT,
            amount_cents BIGINT NOT NULL,
            currency VARCHAR(3) NOT NULL DEFAULT 'CNY',
            occurred_on DATE NOT NULL,
            category VARCHAR(64) NOT NULL,
            memo TEXT NOT NULL DEFAULT '',
            visibility VARCHAR(16) NOT NULL,
            created_by UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
            created_via VARCHAR(16) NOT NULL DEFAULT 'FORM',
            card_id UUID,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_finance_entries_kind CHECK (kind IN ('COST','INCOME')),
            CONSTRAINT ck_finance_entries_scope CHECK (scope IN ('COMPANY','PROJECT')),
            CONSTRAINT ck_finance_entries_visibility
                CHECK (visibility IN ('ALL','MANAGEMENT','OWNER_ONLY')),
            CONSTRAINT ck_finance_entries_created_via CHECK (created_via IN ('FORM','CARD')),
            CONSTRAINT ck_finance_entries_currency CHECK (currency = 'CNY'),
            CONSTRAINT ck_finance_entries_amount
                CHECK (amount_cents BETWEEN 1 AND 1000000000000),
            CONSTRAINT ck_finance_entries_project_scope CHECK (
                (scope = 'PROJECT' AND project_id IS NOT NULL)
                OR (scope = 'COMPANY' AND project_id IS NULL)
            )
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_finance_entries_occurred_on ON finance_entries (occurred_on)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_finance_entries_project ON finance_entries (project_id)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS finance_adjustments (
            id UUID PRIMARY KEY,
            entry_id UUID NOT NULL REFERENCES finance_entries(id) ON DELETE CASCADE,
            field VARCHAR(32) NOT NULL,
            old_value TEXT NOT NULL,
            new_value TEXT NOT NULL,
            reason VARCHAR(500) NOT NULL,
            created_by UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_finance_adjustments_field CHECK (
                field IN ('amount_cents','occurred_on','category','memo','visibility')
            )
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_finance_adjustments_entry ON finance_adjustments (entry_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS finance_adjustments")
    op.execute("DROP TABLE IF EXISTS finance_entries")
