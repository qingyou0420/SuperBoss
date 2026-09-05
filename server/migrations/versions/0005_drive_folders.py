"""Add drive folders and attach files to them.

Revision ID: 0005_drive_folders
Revises: 0004_project_stages
"""

from collections.abc import Sequence

from alembic import op

revision = "0005_drive_folders"
down_revision = "0004_project_stages"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS folders (
            id UUID PRIMARY KEY,
            parent_id UUID REFERENCES folders(id) ON DELETE CASCADE,
            name VARCHAR(128) NOT NULL,
            visibility VARCHAR(16) NOT NULL,
            created_by UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_folders_visibility
                CHECK (visibility IN ('ALL','MANAGEMENT','OWNER_ONLY')),
            CONSTRAINT ck_folders_name_length CHECK (char_length(name) BETWEEN 1 AND 128)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_folders_parent ON folders (parent_id)")
    op.execute("ALTER TABLE files ADD COLUMN IF NOT EXISTS folder_id UUID")
    op.execute(
        """
        DO $$
        DECLARE
            owner_id uuid;
            project_folder uuid;
        BEGIN
            SELECT id INTO owner_id FROM users WHERE role = 'OWNER' LIMIT 1;
            IF owner_id IS NULL THEN
                RETURN;
            END IF;
            INSERT INTO folders (id, parent_id, name, visibility, created_by)
            SELECT gen_random_uuid(), NULL, '项目', 'ALL', owner_id
            WHERE NOT EXISTS (SELECT 1 FROM folders WHERE parent_id IS NULL AND name = '项目')
            RETURNING id INTO project_folder;
            IF project_folder IS NULL THEN
                SELECT id INTO project_folder FROM folders WHERE parent_id IS NULL AND name = '项目' LIMIT 1;
            END IF;
            IF project_folder IS NOT NULL THEN
                UPDATE files SET folder_id = project_folder WHERE folder_id IS NULL;
            END IF;
        END $$;
        """
    )
    op.execute("ALTER TABLE files ALTER COLUMN folder_id SET NOT NULL")
    op.execute(
        "ALTER TABLE files DROP CONSTRAINT IF EXISTS files_folder_id_fkey"
    )
    op.execute(
        "ALTER TABLE files ADD CONSTRAINT files_folder_id_fkey "
        "FOREIGN KEY (folder_id) REFERENCES folders(id) ON DELETE RESTRICT"
    )
    op.execute("ALTER TABLE files DROP CONSTRAINT IF EXISTS uq_files_id_project")
    op.execute("ALTER TABLE files DROP CONSTRAINT IF EXISTS uq_files_upload_idempotency")
    op.execute("ALTER TABLE files DROP CONSTRAINT IF EXISTS ck_files_uploader_kind")
    op.execute("ALTER TABLE files DROP CONSTRAINT IF EXISTS ck_files_category")
    op.execute("ALTER TABLE files ALTER COLUMN project_id DROP NOT NULL")
    op.execute("ALTER TABLE files DROP COLUMN IF EXISTS category")
    op.execute("ALTER TABLE files DROP COLUMN IF EXISTS file_date")
    op.execute("ALTER TABLE files DROP COLUMN IF EXISTS uploader_kind")
    op.execute(
        "ALTER TABLE files ADD CONSTRAINT uq_files_upload_idempotency "
        "UNIQUE (folder_id, uploader_id, idempotency_key)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE files DROP CONSTRAINT IF EXISTS uq_files_upload_idempotency")
    op.execute("ALTER TABLE files ADD COLUMN IF NOT EXISTS category VARCHAR(255) DEFAULT '文档'")
    op.execute("ALTER TABLE files ADD COLUMN IF NOT EXISTS file_date DATE DEFAULT CURRENT_DATE")
    op.execute(
        "ALTER TABLE files ADD COLUMN IF NOT EXISTS uploader_kind VARCHAR(16) DEFAULT 'user'"
    )
    op.execute("ALTER TABLE files DROP CONSTRAINT IF EXISTS files_folder_id_fkey")
    op.execute("ALTER TABLE files DROP COLUMN IF EXISTS folder_id")
    op.execute("DROP TABLE IF EXISTS folders")
