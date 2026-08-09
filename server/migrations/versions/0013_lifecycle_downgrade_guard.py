"""Block destructive lifecycle downgrades while durable work exists.

Revision ID: 0013_lifecycle_downgrade_guard
Revises: 0012_defer_delete_cleanup
"""

from collections.abc import Sequence

from alembic import op

revision = "0013_lifecycle_downgrade_guard"
down_revision = "0012_defer_delete_cleanup"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM uploads WHERE multipart_id IS NULL)
                OR EXISTS (
                    SELECT 1 FROM file_upload_lifecycle
                    WHERE provision_state IN ('PROVISIONING', 'CANCEL_REQUESTED')
                )
                OR EXISTS (
                    SELECT 1 FROM file_upload_lifecycle
                    WHERE completion_state IN ('PREPARED', 'VERIFIED', 'COMPENSATION_PENDING')
                )
                OR EXISTS (
                    SELECT 1 FROM file_lifecycle_outbox WHERE state <> 'DELIVERED'
                )
                OR EXISTS (
                    SELECT 1 FROM file_storage_cleanup WHERE state <> 'DONE'
                )
            THEN
                RAISE EXCEPTION USING
                    MESSAGE = 'SUPERBOSS_FILE_LIFECYCLE_DOWNGRADE_BLOCKED',
                    DETAIL = 'Durable file lifecycle work remains pending.',
                    HINT = 'Deliver or reconcile lifecycle work before retrying the downgrade.';
            END IF;
        END;
        $$;
        """
    )
