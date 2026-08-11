"""Replace WeCom identity with local password credentials.

Revision ID: 0018_local_password_auth
Revises: 0017_import_jobs
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0018_local_password_auth"
down_revision = "0017_import_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _guard_upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM users)
                OR EXISTS (SELECT 1 FROM auth_sessions)
                OR EXISTS (SELECT 1 FROM oauth_states)
            THEN
                RAISE EXCEPTION USING
                    MESSAGE = 'SUPERBOSS_LOCAL_IDENTITY_MIGRATION_BLOCKED',
                    DETAIL = 'Legacy user, session, or OAuth state exists.',
                    HINT = 'Reset development identity data through the approved local procedure.';
            END IF;
        END;
        $$;
        """
    )


def upgrade() -> None:
    _guard_upgrade()
    op.drop_table("oauth_states")
    op.drop_constraint("uq_users_wecom_userid", "users", type_="unique")
    op.drop_column("users", "wecom_userid")
    op.add_column("users", sa.Column("username", sa.String(length=32), nullable=False))
    op.add_column("users", sa.Column("password_hash", sa.String(length=255), nullable=False))
    op.add_column(
        "users",
        sa.Column(
            "must_change_password",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )
    op.add_column(
        "users", sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=False)
    )
    op.add_column(
        "users",
        sa.Column(
            "failed_login_count",
            sa.SmallInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.add_column(
        "users", sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_unique_constraint("uq_users_username", "users", ["username"])
    op.create_check_constraint(
        "ck_users_username",
        "users",
        "username ~ '^[a-z][a-z0-9._-]{2,31}$'",
    )
    op.create_check_constraint(
        "ck_users_password_hash",
        "users",
        r"password_hash ~ '^\$argon2id\$'",
    )
    op.create_check_constraint(
        "ck_users_failed_login_count",
        "users",
        "failed_login_count >= 0 AND failed_login_count <= 32767",
    )


def _guard_downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM users)
                OR EXISTS (SELECT 1 FROM auth_sessions)
            THEN
                RAISE EXCEPTION USING
                    MESSAGE = 'SUPERBOSS_LOCAL_IDENTITY_DOWNGRADE_BLOCKED',
                    DETAIL = 'Local user or session state exists.',
                    HINT = 'Reset local identity data through the approved procedure.';
            END IF;
        END;
        $$;
        """
    )


def downgrade() -> None:
    _guard_downgrade()
    op.drop_constraint("ck_users_failed_login_count", "users", type_="check")
    op.drop_constraint("ck_users_password_hash", "users", type_="check")
    op.drop_constraint("ck_users_username", "users", type_="check")
    op.drop_constraint("uq_users_username", "users", type_="unique")
    op.drop_column("users", "locked_until")
    op.drop_column("users", "failed_login_count")
    op.drop_column("users", "password_changed_at")
    op.drop_column("users", "must_change_password")
    op.drop_column("users", "password_hash")
    op.drop_column("users", "username")
    op.add_column("users", sa.Column("wecom_userid", sa.String(length=255), nullable=False))
    op.create_unique_constraint("uq_users_wecom_userid", "users", ["wecom_userid"])
    op.create_table(
        "oauth_states",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("nonce_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("nonce_hash"),
    )
