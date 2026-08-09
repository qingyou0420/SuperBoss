"""Add resumable file uploads.

Revision ID: 0004_files_and_uploads
Revises: 0003_unique_project_name
"""
from collections.abc import Sequence
import sqlalchemy as sa
from alembic import op
revision="0004_files_and_uploads"; down_revision="0003_unique_project_name"; branch_labels: str|Sequence[str]|None=None; depends_on: str|Sequence[str]|None=None
def upgrade()->None:
 op.create_table("files",sa.Column("id",sa.UUID(),primary_key=True),sa.Column("project_id",sa.UUID(),sa.ForeignKey("projects.id",ondelete="CASCADE"),nullable=False),sa.Column("filename",sa.String(1024),nullable=False),sa.Column("category",sa.String(255),nullable=False),sa.Column("file_date",sa.Date(),nullable=False),sa.Column("object_key",sa.String(2048),nullable=False,unique=True),sa.Column("size_bytes",sa.Integer(),nullable=False),sa.Column("sha256",sa.String(64),nullable=False),sa.Column("state",sa.String(32),nullable=False),sa.Column("uploader_id",sa.UUID(),sa.ForeignKey("users.id"),nullable=False),sa.Column("scan_result",sa.Text()),sa.Column("created_at",sa.DateTime(timezone=True),server_default=sa.text("now()"),nullable=False),sa.Column("updated_at",sa.DateTime(timezone=True),server_default=sa.text("now()"),nullable=False))
 op.create_table("uploads",sa.Column("id",sa.UUID(),primary_key=True),sa.Column("file_id",sa.UUID(),sa.ForeignKey("files.id",ondelete="CASCADE"),nullable=False,unique=True),sa.Column("project_id",sa.UUID(),nullable=False),sa.Column("uploader_id",sa.UUID(),nullable=False),sa.Column("idempotency_key",sa.String(255),nullable=False),sa.Column("multipart_id",sa.String(1024),nullable=False),sa.Column("created_at",sa.DateTime(timezone=True),server_default=sa.text("now()"),nullable=False),sa.UniqueConstraint("project_id","uploader_id","idempotency_key",name="uq_upload_idempotency"))
def downgrade()->None: op.drop_table("uploads");op.drop_table("files")
