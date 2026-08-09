"""Add lease ownership to cleanup execution.

Revision ID: 0010_cleanup_claim_lease
Revises: 0009_outbox_claim_lease
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0010_cleanup_claim_lease"
down_revision = "0009_outbox_claim_lease"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("file_storage_cleanup", sa.Column("claim_token", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_index("ix_file_storage_cleanup_due_lease", "file_storage_cleanup", ["state", "next_attempt_at", "locked_at"])


def downgrade() -> None:
    op.drop_index("ix_file_storage_cleanup_due_lease", table_name="file_storage_cleanup")
    op.drop_column("file_storage_cleanup", "claim_token")
