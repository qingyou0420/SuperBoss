"""Add lease ownership to completion outbox delivery.

Revision ID: 0009_outbox_claim_lease
Revises: 0008_file_delete_cleanup
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0009_outbox_claim_lease"
down_revision = "0008_file_delete_cleanup"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("file_lifecycle_outbox", sa.Column("claim_token", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_index("ix_file_lifecycle_outbox_due_lease", "file_lifecycle_outbox", ["state", "next_attempt_at", "locked_at"])


def downgrade() -> None:
    op.drop_index("ix_file_lifecycle_outbox_due_lease", table_name="file_lifecycle_outbox")
    op.drop_column("file_lifecycle_outbox", "claim_token")
