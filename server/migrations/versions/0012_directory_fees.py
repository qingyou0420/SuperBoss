"""Directory entries, communications, project fee and lead.

Revision ID: 0012_directory_fees
Revises: 0011_backfill_seq
"""

from collections.abc import Sequence

from alembic import op

revision = "0012_directory_fees"
down_revision = "0011_backfill_seq"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS directory_entries (
            id UUID PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            district VARCHAR(64) NOT NULL DEFAULT '',
            street VARCHAR(64) NOT NULL DEFAULT '',
            community VARCHAR(64) NOT NULL DEFAULT '',
            property_company VARCHAR(255) NOT NULL DEFAULT '',
            households INTEGER,
            floor_area VARCHAR(64) NOT NULL DEFAULT '',
            delivered_on DATE,
            estate_type VARCHAR(64) NOT NULL DEFAULT '',
            manager_name VARCHAR(64) NOT NULL DEFAULT '',
            phone VARCHAR(32) NOT NULL DEFAULT '',
            extra JSONB NOT NULL DEFAULT '{}'::jsonb,
            source_filename VARCHAR(255) NOT NULL,
            source_sheet VARCHAR(64) NOT NULL DEFAULT 'Sheet1',
            source_row INTEGER NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_directory_entries_name_length
                CHECK (char_length(name) BETWEEN 1 AND 255),
            CONSTRAINT ck_directory_entries_source_row CHECK (source_row >= 1),
            CONSTRAINT uq_directory_entries_source
                UNIQUE (source_filename, source_sheet, source_row)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_directory_entries_area "
        "ON directory_entries (district, street, community)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS directory_communications (
            id UUID PRIMARY KEY,
            entry_id UUID NOT NULL REFERENCES directory_entries(id) ON DELETE CASCADE,
            occurred_on DATE NOT NULL,
            contact_name VARCHAR(64) NOT NULL DEFAULT '',
            contact_role VARCHAR(64) NOT NULL DEFAULT '',
            demand TEXT NOT NULL DEFAULT '',
            result TEXT NOT NULL DEFAULT '',
            next_step TEXT NOT NULL DEFAULT '',
            status VARCHAR(32) NOT NULL DEFAULT 'LEARNING',
            created_by UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_directory_communications_status CHECK (
                status IN (
                    'LEARNING','CONTACTING','COMMISSIONED','PAUSED','DROPPED'
                )
            )
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_directory_communications_entry "
        "ON directory_communications (entry_id, occurred_on DESC)"
    )
    op.execute("ALTER TABLE projects ADD COLUMN IF NOT EXISTS service_fee_cents BIGINT")
    op.execute(
        "ALTER TABLE projects ADD COLUMN IF NOT EXISTS lead_user_id UUID "
        "REFERENCES users(id) ON DELETE SET NULL"
    )
    op.execute("ALTER TABLE projects DROP CONSTRAINT IF EXISTS ck_projects_service_fee")
    op.execute(
        "ALTER TABLE projects ADD CONSTRAINT ck_projects_service_fee "
        "CHECK (service_fee_cents IS NULL OR service_fee_cents >= 0)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE projects DROP CONSTRAINT IF EXISTS ck_projects_service_fee")
    op.execute("ALTER TABLE projects DROP COLUMN IF EXISTS lead_user_id")
    op.execute("ALTER TABLE projects DROP COLUMN IF EXISTS service_fee_cents")
    op.execute("DROP TABLE IF EXISTS directory_communications")
    op.execute("DROP TABLE IF EXISTS directory_entries")
