"""Drop device pairing, Kimi import jobs, and device sessions.

Revision ID: 0002_drop_devices_and_imports
Revises: 0001_baseline
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision = "0002_drop_devices_and_imports"
down_revision = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEVICE_TABLES = (
    "import_attachments",
    "import_jobs",
    "device_scope_grants",
    "device_project_grants",
    "device_pairing_projects",
    "device_pairing_codes",
    "device_connections",
)


def _columns(table: str) -> set[str]:
    rows = op.get_bind().execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = :table_name"
        ),
        {"table_name": table},
    )
    return {row[0] for row in rows}


def upgrade() -> None:
    columns = _columns("sessions")
    if "kind" in columns:
        op.execute("DELETE FROM sessions WHERE kind = 'device'")
        op.execute("ALTER TABLE sessions DROP CONSTRAINT IF EXISTS ck_sessions_kind")
        op.execute("ALTER TABLE sessions DROP CONSTRAINT IF EXISTS ck_sessions_subject")
        op.execute("DROP INDEX IF EXISTS ix_sessions_device_created")
        op.execute("ALTER TABLE sessions DROP COLUMN IF EXISTS device_id")
        op.execute("ALTER TABLE sessions DROP COLUMN IF EXISTS kind")
        op.execute("ALTER TABLE sessions ALTER COLUMN user_id SET NOT NULL")
    for table in _DEVICE_TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute("ALTER TABLE files DROP CONSTRAINT IF EXISTS ck_files_uploader_kind")
    if "uploader_kind" in _columns("files"):
        op.execute(
            "ALTER TABLE files ADD CONSTRAINT ck_files_uploader_kind "
            "CHECK (uploader_kind IN ('user','system'))"
        )


def downgrade() -> None:
    op.execute("ALTER TABLE files DROP CONSTRAINT IF EXISTS ck_files_uploader_kind")
    if "uploader_kind" in _columns("files"):
        op.execute(
            "ALTER TABLE files ADD CONSTRAINT ck_files_uploader_kind "
            "CHECK (uploader_kind IN ('user','device','system'))"
        )
    columns = _columns("sessions")
    if "kind" not in columns:
        op.execute("ALTER TABLE sessions ALTER COLUMN user_id DROP NOT NULL")
        op.execute("ALTER TABLE sessions ADD COLUMN kind VARCHAR(16) NOT NULL DEFAULT 'user'")
        op.execute("ALTER TABLE sessions ADD COLUMN device_id UUID")
        op.execute(
            "ALTER TABLE sessions ADD CONSTRAINT ck_sessions_kind "
            "CHECK (kind IN ('user','device'))"
        )
        op.execute(
            "ALTER TABLE sessions ADD CONSTRAINT ck_sessions_subject CHECK ("
            "(kind = 'user' AND user_id IS NOT NULL AND device_id IS NULL) OR "
            "(kind = 'device' AND device_id IS NOT NULL AND user_id IS NULL))"
        )
        op.execute(
            "CREATE INDEX ix_sessions_device_created ON sessions (device_id, created_at)"
        )
