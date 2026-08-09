"""Enforce upload/file project parity.

Revision ID: 0005_file_lifecycle
Revises: 0004_files_and_uploads
"""

from collections.abc import Sequence

from alembic import op

revision = "0005_file_lifecycle"
down_revision = "0004_files_and_uploads"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint("uq_files_id_project", "files", ["id", "project_id"])
    op.drop_constraint("uploads_file_id_fkey", "uploads", type_="foreignkey")
    op.create_foreign_key(
        "fk_uploads_file_project",
        "uploads",
        "files",
        ["file_id", "project_id"],
        ["id", "project_id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint("fk_uploads_file_project", "uploads", type_="foreignkey")
    op.create_foreign_key(
        "uploads_file_id_fkey", "uploads", "files", ["file_id"], ["id"], ondelete="CASCADE"
    )
    op.drop_constraint("uq_files_id_project", "files", type_="unique")
