"""Persist completion replay intent and finalization outbox.

Revision ID: 0007_completion_recovery
Revises: 0006_file_lifecycle_recovery
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007_completion_recovery"
down_revision = "0006_file_lifecycle_recovery"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("file_upload_lifecycle", sa.Column("canonical_parts_json", postgresql.JSONB(), nullable=True))
    op.add_column("file_upload_lifecycle", sa.Column("completion_actor_kind", sa.String(length=16), nullable=True))
    op.add_column("file_upload_lifecycle", sa.Column("completion_actor_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("file_upload_lifecycle", sa.Column("completion_actor_role", sa.String(length=16), nullable=True))
    op.add_column("file_upload_lifecycle", sa.Column("completion_request_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("file_upload_lifecycle", sa.Column("prepared_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("file_upload_lifecycle", "prepared_at")
    op.drop_column("file_upload_lifecycle", "completion_request_id")
    op.drop_column("file_upload_lifecycle", "completion_actor_role")
    op.drop_column("file_upload_lifecycle", "completion_actor_id")
    op.drop_column("file_upload_lifecycle", "completion_actor_kind")
    op.drop_column("file_upload_lifecycle", "canonical_parts_json")
