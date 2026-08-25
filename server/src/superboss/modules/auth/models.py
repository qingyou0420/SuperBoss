"""Persistent server-side session records for browser users and devices."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from superboss.core.db import Base


class SessionKind(StrEnum):
    USER = "user"
    DEVICE = "device"


class AuthSession(Base):
    __tablename__ = "sessions"
    __table_args__ = (
        CheckConstraint("kind IN ('user','device')", name="ck_sessions_kind"),
        CheckConstraint(
            "(kind = 'user' AND user_id IS NOT NULL AND device_id IS NULL) OR "
            "(kind = 'device' AND device_id IS NOT NULL AND user_id IS NULL)",
            name="ck_sessions_subject",
        ),
        CheckConstraint(
            "refresh_token_hash ~ '^[0-9a-f]{64}$'",
            name="ck_sessions_refresh_hash",
        ),
        Index("ix_sessions_user_created", "user_id", "created_at"),
        Index("ix_sessions_device_created", "device_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    kind: Mapped[SessionKind] = mapped_column(
        Enum(SessionKind, name="session_kind", native_enum=False),
        default=SessionKind.USER,
        nullable=False,
    )
    user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
    )
    device_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("device_connections.id", ondelete="CASCADE"),
        nullable=True,
    )
    access_jti: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    refresh_token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    access_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    refresh_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    refresh_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
