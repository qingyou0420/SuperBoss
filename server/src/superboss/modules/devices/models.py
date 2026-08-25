"""Normalized persistence for device pairing, sessions, and grants."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from superboss.core.db import Base
from superboss.modules.auth.models import AuthSession as DeviceSession  # noqa: F401


class DevicePairingCode(Base):
    __tablename__ = "device_pairing_codes"
    __table_args__ = (
        CheckConstraint(
            "code_hash ~ '^[0-9a-f]{64}$'", name="ck_device_pairing_codes_hash"
        ),
        CheckConstraint(
            "expires_at > created_at", name="ck_device_pairing_codes_expiry_order"
        ),
        CheckConstraint(
            "used_at IS NULL OR (used_at >= created_at AND used_at <= expires_at)",
            name="ck_device_pairing_codes_used_order",
        ),
        Index("ix_device_pairing_codes_owner_created", "owner_id", "created_at"),
        Index("ix_device_pairing_codes_expires_at", "expires_at"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class DevicePairingProject(Base):
    __tablename__ = "device_pairing_projects"
    __table_args__ = (Index("ix_device_pairing_projects_project", "project_id"),)

    pairing_code_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("device_pairing_codes.id", ondelete="CASCADE"),
        primary_key=True,
    )
    project_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("projects.id", ondelete="RESTRICT"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class DeviceConnection(Base):
    __tablename__ = "device_connections"
    __table_args__ = (
        CheckConstraint(
            "name = btrim(name, E' \\t\\r\\n' || chr(160))",
            name="ck_device_connections_name_trimmed",
        ),
        CheckConstraint(
            "char_length(name) BETWEEN 1 AND 128", name="ck_device_connections_name_length"
        ),
        CheckConstraint(
            "last_used_at IS NULL OR last_used_at >= paired_at",
            name="ck_device_connections_last_used_order",
        ),
        CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= paired_at",
            name="ck_device_connections_revoked_order",
        ),
        Index("ix_device_connections_owner_paired", "owner_id", "paired_at"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    paired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DeviceProjectGrant(Base):
    __tablename__ = "device_project_grants"
    __table_args__ = (Index("ix_device_project_grants_project", "project_id"),)

    device_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("device_connections.id", ondelete="CASCADE"),
        primary_key=True,
    )
    project_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("projects.id", ondelete="RESTRICT"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class DeviceScopeGrant(Base):
    __tablename__ = "device_scope_grants"
    __table_args__ = (
        CheckConstraint(
            "scope IN ('imports:create','imports:read-own','imports:submit','imports:upload')",
            name="ck_device_scope_grants_scope",
        ),
    )

    device_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("device_connections.id", ondelete="CASCADE"),
        primary_key=True,
    )
    scope: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
