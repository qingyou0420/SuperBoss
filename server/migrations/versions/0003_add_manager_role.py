"""Allow MANAGER as a first-class account role.

Revision ID: 0003_add_manager_role
Revises: 0002_drop_devices_and_imports
"""

from collections.abc import Sequence

from alembic import op

revision = "0003_add_manager_role"
down_revision = "0002_drop_devices_and_imports"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS ck_users_role")
    op.execute(
        "ALTER TABLE users ADD CONSTRAINT ck_users_role "
        "CHECK (role IN ('OWNER', 'MANAGER', 'STAFF'))"
    )


def downgrade() -> None:
    op.execute("UPDATE users SET role = 'STAFF' WHERE role = 'MANAGER'")
    op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS ck_users_role")
    op.execute(
        "ALTER TABLE users ADD CONSTRAINT ck_users_role "
        "CHECK (role IN ('OWNER', 'STAFF'))"
    )
