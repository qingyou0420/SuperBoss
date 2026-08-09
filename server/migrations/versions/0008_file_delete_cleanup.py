"""Snapshot durable storage cleanup before file deletion.

Revision ID: 0008_file_delete_cleanup
Revises: 0007_completion_recovery
"""

from collections.abc import Sequence

from alembic import op

revision = "0008_file_delete_cleanup"
down_revision = "0007_completion_recovery"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE UNIQUE INDEX uq_file_storage_cleanup_operation_target "
        "ON file_storage_cleanup (operation, object_key, COALESCE(multipart_id, ''))"
    )
    op.execute(
        """
        CREATE FUNCTION snapshot_file_storage_cleanup() RETURNS trigger AS $$
        DECLARE lifecycle record;
        BEGIN
            FOR lifecycle IN
                SELECT upload_id, object_key, multipart_id
                FROM file_upload_lifecycle WHERE file_id = OLD.id
            LOOP
                INSERT INTO file_storage_cleanup
                    (id, operation, dedupe_key, object_key, multipart_id, lifecycle_id)
                VALUES
                    (md5(random()::text || clock_timestamp()::text)::uuid,
                     'DELETE_OBJECT',
                     lpad(md5('DELETE_OBJECT' || E'\\x1f' || lifecycle.object_key), 64, '0'),
                     lifecycle.object_key, NULL, lifecycle.upload_id)
                ON CONFLICT DO NOTHING;
                IF lifecycle.multipart_id IS NOT NULL THEN
                    INSERT INTO file_storage_cleanup
                        (id, operation, dedupe_key, object_key, multipart_id, lifecycle_id)
                    VALUES
                        (md5(random()::text || clock_timestamp()::text)::uuid,
                         'ABORT_MULTIPART',
                         lpad(md5('ABORT_MULTIPART' || E'\\x1f' || lifecycle.object_key || E'\\x1f' || lifecycle.multipart_id), 64, '0'),
                         lifecycle.object_key, lifecycle.multipart_id, lifecycle.upload_id)
                    ON CONFLICT DO NOTHING;
                END IF;
                UPDATE file_upload_lifecycle
                SET provision_state = 'CANCEL_REQUESTED'
                WHERE upload_id = lifecycle.upload_id;
            END LOOP;
            RETURN OLD;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER trg_files_snapshot_storage_cleanup BEFORE DELETE ON files "
        "FOR EACH ROW EXECUTE FUNCTION snapshot_file_storage_cleanup()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_files_snapshot_storage_cleanup ON files")
    op.execute("DROP FUNCTION snapshot_file_storage_cleanup()")
    op.execute("DROP INDEX uq_file_storage_cleanup_operation_target")
