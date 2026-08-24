"""Drop file lifecycle outbox, cleanup, and claim tables.

Revision ID: 0019_drop_file_lifecycle
Revises: 0018_local_password_auth
"""

from collections.abc import Sequence

from alembic import op

revision = "0019_drop_file_lifecycle"
down_revision = "0018_local_password_auth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_files_snapshot_storage_cleanup ON files")
    op.execute("DROP FUNCTION IF EXISTS snapshot_file_storage_cleanup()")
    op.execute("DROP TABLE IF EXISTS file_lifecycle_outbox CASCADE")
    op.execute("DROP TABLE IF EXISTS file_storage_cleanup CASCADE")
    op.execute("DROP TABLE IF EXISTS file_upload_lifecycle CASCADE")
    op.execute("DROP TABLE IF EXISTS import_idempotency_claims CASCADE")


def downgrade() -> None:
    raise RuntimeError("file lifecycle tables are not restored")
