"""Persist retry coordinates for ambiguous multipart completion.

Revision ID: 0011_completion_ambiguity_retry
Revises: 0010_cleanup_claim_lease
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "0011_completion_ambiguity_retry"
down_revision = "0010_cleanup_claim_lease"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("file_upload_lifecycle", sa.Column("completion_attempt_count", sa.Integer(), server_default="0", nullable=False))
    op.add_column("file_upload_lifecycle", sa.Column("completion_next_attempt_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("file_upload_lifecycle", sa.Column("completion_last_error_code", sa.String(length=64), nullable=True))
    op.create_check_constraint("ck_file_upload_lifecycle_completion_attempt_count", "file_upload_lifecycle", "completion_attempt_count >= 0")
    op.create_check_constraint("ck_file_upload_lifecycle_completion_error_code", "file_upload_lifecycle", "completion_last_error_code IS NULL OR completion_last_error_code IN ('COMPLETION_AMBIGUOUS')")
    op.create_index("ix_file_upload_lifecycle_completion_due", "file_upload_lifecycle", ["completion_state", "completion_next_attempt_at"])


def downgrade() -> None:
    op.drop_index("ix_file_upload_lifecycle_completion_due", table_name="file_upload_lifecycle")
    op.drop_constraint("ck_file_upload_lifecycle_completion_error_code", "file_upload_lifecycle", type_="check")
    op.drop_constraint("ck_file_upload_lifecycle_completion_attempt_count", "file_upload_lifecycle", type_="check")
    op.drop_column("file_upload_lifecycle", "completion_last_error_code")
    op.drop_column("file_upload_lifecycle", "completion_next_attempt_at")
    op.drop_column("file_upload_lifecycle", "completion_attempt_count")
