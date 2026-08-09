"""Defer delete cleanup while multipart completion remains ambiguous.

Revision ID: 0012_defer_ambiguous_delete_cleanup
Revises: 0011_completion_ambiguity_retry
"""

from collections.abc import Sequence

from alembic import op

revision = "0012_defer_delete_cleanup"
down_revision = "0011_completion_ambiguity_retry"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION snapshot_file_storage_cleanup() RETURNS trigger AS $$
        DECLARE lifecycle record;
        DECLARE cleanup_next_attempt_at timestamptz;
        BEGIN
            FOR lifecycle IN
                SELECT upload_id, object_key, multipart_id, completion_state,
                       completion_next_attempt_at
                FROM file_upload_lifecycle WHERE file_id = OLD.id
            LOOP
                IF lifecycle.completion_state = 'PREPARED' THEN
                    cleanup_next_attempt_at := GREATEST(
                        COALESCE(
                            lifecycle.completion_next_attempt_at,
                            clock_timestamp() + interval '120 seconds'
                        ),
                        clock_timestamp()
                    );
                ELSE
                    cleanup_next_attempt_at := clock_timestamp();
                END IF;
                INSERT INTO file_storage_cleanup
                    (id, operation, dedupe_key, object_key, multipart_id, lifecycle_id,
                     next_attempt_at)
                VALUES
                    (md5(random()::text || clock_timestamp()::text)::uuid,
                     'DELETE_OBJECT',
                     lpad(md5('DELETE_OBJECT' || E'\\x1f' || lifecycle.object_key), 64, '0'),
                     lifecycle.object_key, NULL, lifecycle.upload_id, cleanup_next_attempt_at)
                ON CONFLICT DO NOTHING;
                IF lifecycle.multipart_id IS NOT NULL THEN
                    INSERT INTO file_storage_cleanup
                        (id, operation, dedupe_key, object_key, multipart_id, lifecycle_id,
                         next_attempt_at)
                    VALUES
                        (md5(random()::text || clock_timestamp()::text)::uuid,
                         'ABORT_MULTIPART',
                         lpad(md5('ABORT_MULTIPART' || E'\\x1f' || lifecycle.object_key || E'\\x1f' || lifecycle.multipart_id), 64, '0'),
                         lifecycle.object_key, lifecycle.multipart_id, lifecycle.upload_id,
                         cleanup_next_attempt_at)
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


def downgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION snapshot_file_storage_cleanup() RETURNS trigger AS $$
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
