"""Atomic device pairing, rotation, revocation, and live authorization."""

import unicodedata
from collections.abc import Callable, Collection
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from superboss.core.actors import Actor
from superboss.core.config import Settings
from superboss.core.security import (
    DEVICE_ACCESS_SCOPES,
    TokenError,
    decode_device_access_token,
    hash_token,
    issue_device_access_token,
    new_opaque_token,
)
from superboss.modules.audit.models import AuditLog
from superboss.modules.devices.models import (
    DeviceConnection,
    DevicePairingCode,
    DevicePairingProject,
    DeviceProjectGrant,
    DeviceScopeGrant,
    DeviceSession,
)
from superboss.modules.projects.models import Project, ProjectStatus
from superboss.modules.users.models import Role, User, UserStatus


class InvalidDeviceGrant(Exception):
    """Requested device authorization is not allowed."""


class InvalidDeviceCredential(Exception):
    """Device credential failed without disclosing the matching condition."""

    def __init__(self) -> None:
        super().__init__("Device credential is invalid")


@dataclass(frozen=True)
class PairingCodeIssue:
    raw_code: str
    expires_at: datetime


@dataclass(frozen=True)
class DeviceTokenPair:
    device_id: UUID
    access_token: str
    refresh_token: str
    token_type: str
    expires_at: datetime
    refresh_expires_at: datetime


@dataclass(frozen=True)
class _CredentialRejected(Exception):
    object_id: UUID | None = None


def _utcnow() -> datetime:
    return datetime.now(UTC)


class DeviceService:
    """Own short transactions so credential state and success audit remain inseparable."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        *,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.clock = clock

    def _now(self) -> datetime:
        now = self.clock()
        if now.tzinfo is None:
            raise ValueError("device service clock must be timezone-aware")
        return now

    @staticmethod
    def _credential_hash(raw_credential: str) -> str | None:
        if not isinstance(raw_credential, str) or not 32 <= len(raw_credential) <= 512:
            return None
        return hash_token(raw_credential)

    @staticmethod
    def _device_name(value: str) -> str:
        normalized = unicodedata.normalize("NFKC", value).strip(" \t\r\n\u00a0")
        if not 1 <= len(normalized) <= 128:
            raise InvalidDeviceGrant("Device name is invalid")
        return normalized

    @staticmethod
    def _event_key(kind: str, object_id: UUID) -> UUID:
        return uuid5(NAMESPACE_URL, f"superboss:{kind}:{object_id}")

    @staticmethod
    async def _audit(
        session: AsyncSession,
        *,
        actor_kind: str,
        actor_id: UUID | None,
        actor_role: Role | None,
        action: str,
        object_type: str,
        object_id: UUID | None,
        outcome: str,
        request_id: UUID,
        metadata: dict[str, object],
        event_key: UUID | None = None,
    ) -> None:
        expected_metadata = {
            **metadata,
            "actor_role": actor_role.value if actor_role is not None else None,
        }
        if event_key is not None:
            existing = await session.scalar(
                select(AuditLog).where(AuditLog.event_key == event_key)
            )
            if existing is not None:
                if (
                    existing.actor_kind == actor_kind
                    and existing.actor_id == actor_id
                    and existing.action == action
                    and existing.object_type == object_type
                    and existing.object_id == object_id
                    and existing.project_id is None
                    and existing.outcome == outcome
                    and existing.request_id == request_id
                    and existing.metadata_json == expected_metadata
                ):
                    return
                raise RuntimeError("device audit event conflicts with immutable evidence")
        session.add(
            AuditLog(
                actor_kind=actor_kind,
                actor_id=actor_id,
                action=action,
                object_type=object_type,
                object_id=object_id,
                project_id=None,
                outcome=outcome,
                metadata_json=expected_metadata,
                request_id=request_id,
                event_key=event_key,
            )
        )
        await session.flush()

    async def _denied(self, action: str, request_id: UUID, object_id: UUID | None) -> None:
        async with self.session_factory() as session, session.begin():
            await self._audit(
                session,
                actor_kind="system",
                actor_id=None,
                actor_role=None,
                action=action,
                object_type="device",
                object_id=object_id,
                outcome="DENIED",
                request_id=request_id,
                metadata={"reason": "INVALID_CREDENTIAL"},
            )

    async def create_pairing_code(
        self,
        owner_id: UUID,
        project_ids: Collection[UUID],
        *,
        request_id: UUID,
    ) -> PairingCodeIssue:
        selected = tuple(project_ids)
        if not selected or len(set(selected)) != len(selected):
            raise InvalidDeviceGrant("At least one unique active project is required")
        now = self._now()
        raw_code = new_opaque_token()
        expires_at = now + timedelta(minutes=10)
        async with self.session_factory() as session, session.begin():
            owner = await session.scalar(
                select(User).where(User.id == owner_id).with_for_update()
            )
            if (
                owner is None
                or owner.role != Role.OWNER
                or owner.status != UserStatus.ACTIVE
            ):
                raise InvalidDeviceGrant("Active OWNER is required")
            projects = list(
                await session.scalars(
                    select(Project)
                    .where(Project.id.in_(selected))
                    .with_for_update()
                )
            )
            if len(projects) != len(selected) or any(
                project.status != ProjectStatus.ACTIVE for project in projects
            ):
                raise InvalidDeviceGrant("At least one unique active project is required")
            pairing = DevicePairingCode(
                owner_id=owner.id,
                code_hash=hash_token(raw_code),
                created_at=now,
                expires_at=expires_at,
            )
            session.add(pairing)
            await session.flush()
            session.add_all(
                DevicePairingProject(pairing_code_id=pairing.id, project_id=project_id)
                for project_id in selected
            )
            await self._audit(
                session,
                actor_kind="user",
                actor_id=owner.id,
                actor_role=Role.OWNER,
                action="device.pairing_code.create",
                object_type="device_pairing_code",
                object_id=pairing.id,
                outcome="SUCCESS",
                request_id=request_id,
                event_key=self._event_key("device-pairing-code-create", pairing.id),
                metadata={"project_count": len(selected)},
            )
        return PairingCodeIssue(raw_code, expires_at)

    async def pair(
        self, raw_code: str, device_name: str, *, request_id: UUID
    ) -> DeviceTokenPair:
        normalized_name = self._device_name(device_name)
        credential_hash = self._credential_hash(raw_code)
        now = self._now()
        try:
            async with self.session_factory() as session, session.begin():
                pairing = None
                if credential_hash is not None:
                    pairing = await session.scalar(
                        select(DevicePairingCode)
                        .where(DevicePairingCode.code_hash == credential_hash)
                        .with_for_update()
                    )
                if (
                    pairing is None
                    or pairing.used_at is not None
                    or now >= pairing.expires_at
                ):
                    raise _CredentialRejected(pairing.id if pairing is not None else None)
                owner = await session.scalar(
                    select(User).where(User.id == pairing.owner_id).with_for_update()
                )
                if (
                    owner is None
                    or owner.role != Role.OWNER
                    or owner.status != UserStatus.ACTIVE
                ):
                    raise _CredentialRejected(pairing.id)
                projects = list(
                    await session.scalars(
                        select(Project)
                        .join(
                            DevicePairingProject,
                            DevicePairingProject.project_id == Project.id,
                        )
                        .where(DevicePairingProject.pairing_code_id == pairing.id)
                        .with_for_update()
                    )
                )
                if not projects or any(
                    project.status != ProjectStatus.ACTIVE for project in projects
                ):
                    raise _CredentialRejected(pairing.id)
                pairing.used_at = now
                device = DeviceConnection(
                    owner_id=owner.id, name=normalized_name, paired_at=now
                )
                session.add(device)
                await session.flush()
                session.add_all(
                    DeviceProjectGrant(device_id=device.id, project_id=project.id)
                    for project in projects
                )
                session.add_all(
                    DeviceScopeGrant(device_id=device.id, scope=scope)
                    for scope in DEVICE_ACCESS_SCOPES
                )
                token_pair = await self._new_session(session, device, now)
                await self._audit(
                    session,
                    actor_kind="device",
                    actor_id=device.id,
                    actor_role=None,
                    action="device.pair",
                    object_type="device",
                    object_id=device.id,
                    outcome="SUCCESS",
                    request_id=request_id,
                    event_key=self._event_key("device-pair", pairing.id),
                    metadata={"state": "ACTIVE", "project_count": len(projects)},
                )
                return token_pair
        except _CredentialRejected as error:
            await self._denied("device.pair", request_id, error.object_id)
            raise InvalidDeviceCredential() from None

    async def _new_session(
        self, session: AsyncSession, device: DeviceConnection, now: datetime
    ) -> DeviceTokenPair:
        token_issued_at = now.replace(microsecond=0)
        raw_refresh = new_opaque_token()
        session_id = uuid4()
        access_jti = uuid4()
        access_token, access_expires_at = issue_device_access_token(
            self.settings,
            device_id=device.id,
            owner_id=device.owner_id,
            session_id=session_id,
            access_jti=access_jti,
            issued_at=token_issued_at,
        )
        refresh_expires_at = token_issued_at + timedelta(days=14)
        session.add(
            DeviceSession(
                id=session_id,
                device_id=device.id,
                access_jti=access_jti,
                refresh_token_hash=hash_token(raw_refresh),
                access_expires_at=access_expires_at,
                refresh_expires_at=refresh_expires_at,
                created_at=token_issued_at,
            )
        )
        await session.flush()
        return DeviceTokenPair(
            device.id,
            access_token,
            raw_refresh,
            "Bearer",
            access_expires_at,
            refresh_expires_at,
        )

    async def refresh(self, raw_refresh: str, *, request_id: UUID) -> DeviceTokenPair:
        credential_hash = self._credential_hash(raw_refresh)
        now = self._now()
        try:
            async with self.session_factory() as session, session.begin():
                locator = None
                if credential_hash is not None:
                    locator = await session.scalar(
                        select(DeviceSession).where(
                            DeviceSession.refresh_token_hash == credential_hash
                        )
                    )
                if locator is None:
                    raise _CredentialRejected()
                device_locator = await session.get(DeviceConnection, locator.device_id)
                if device_locator is None:
                    raise _CredentialRejected(locator.device_id)
                owner = await session.scalar(
                    select(User).where(User.id == device_locator.owner_id).with_for_update()
                )
                device = await session.scalar(
                    select(DeviceConnection)
                    .where(DeviceConnection.id == locator.device_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                current = await session.scalar(
                    select(DeviceSession)
                    .where(DeviceSession.refresh_token_hash == credential_hash)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if (
                    owner is None
                    or owner.role != Role.OWNER
                    or owner.status != UserStatus.ACTIVE
                    or device is None
                    or device.owner_id != owner.id
                    or device.revoked_at is not None
                    or current is None
                    or current.id != locator.id
                    or current.device_id != device.id
                    or current.refresh_used_at is not None
                    or current.revoked_at is not None
                    or now >= current.refresh_expires_at
                ):
                    raise _CredentialRejected(locator.device_id)
                current.refresh_used_at = now
                current.revoked_at = now
                token_pair = await self._new_session(session, device, now)
                await self._audit(
                    session,
                    actor_kind="device",
                    actor_id=device.id,
                    actor_role=None,
                    action="device.refresh",
                    object_type="device",
                    object_id=device.id,
                    outcome="SUCCESS",
                    request_id=request_id,
                    event_key=self._event_key("device-refresh", current.id),
                    metadata={"state": "ACTIVE"},
                )
                return token_pair
        except _CredentialRejected as error:
            await self._denied("device.refresh", request_id, error.object_id)
            raise InvalidDeviceCredential() from None

    async def authenticate_access_token(
        self, raw_access: str, *, request_id: UUID
    ) -> Actor:
        try:
            claims = decode_device_access_token(self.settings, raw_access)
            device_id = UUID(str(claims["device_id"]))
            owner_id = UUID(str(claims["owner_id"]))
            session_id = UUID(str(claims["session_id"]))
            access_jti = UUID(str(claims["jti"]))
            issued_at = datetime.fromtimestamp(cast(int, claims["iat"]), UTC)
            expires_at = datetime.fromtimestamp(cast(int, claims["exp"]), UTC)
        except (TokenError, TypeError, ValueError):
            raise InvalidDeviceCredential() from None
        now = self._now()
        async with self.session_factory() as session, session.begin():
            owner = await session.scalar(
                select(User).where(User.id == owner_id).with_for_update()
            )
            device = await session.scalar(
                select(DeviceConnection)
                .where(DeviceConnection.id == device_id)
                .with_for_update()
            )
            device_session = await session.scalar(
                select(DeviceSession)
                .where(DeviceSession.id == session_id)
                .with_for_update()
            )
            if (
                owner is None
                or owner.role != Role.OWNER
                or owner.status != UserStatus.ACTIVE
                or device is None
                or device.owner_id != owner_id
                or device.revoked_at is not None
                or device_session is None
                or device_session.device_id != device_id
                or device_session.access_jti != access_jti
                or device_session.revoked_at is not None
                or device_session.created_at != issued_at
                or device_session.access_expires_at != expires_at
                or now >= device_session.access_expires_at
                or now < issued_at
                or now >= expires_at
            ):
                raise InvalidDeviceCredential()
            project_ids = frozenset(
                await session.scalars(
                    select(DeviceProjectGrant.project_id)
                    .join(Project, Project.id == DeviceProjectGrant.project_id)
                    .where(
                        DeviceProjectGrant.device_id == device.id,
                        Project.status == ProjectStatus.ACTIVE,
                    )
                )
            )
            current_scopes = frozenset(
                await session.scalars(
                    select(DeviceScopeGrant.scope).where(
                        DeviceScopeGrant.device_id == device.id
                    )
                )
            )
            effective_scopes = current_scopes & frozenset(DEVICE_ACCESS_SCOPES)
            if device.last_used_at is None or now > device.last_used_at:
                device.last_used_at = now
            await self._audit(
                session,
                actor_kind="device",
                actor_id=device.id,
                actor_role=None,
                action="device.use",
                object_type="device",
                object_id=device.id,
                outcome="SUCCESS",
                request_id=request_id,
                event_key=self._event_key("device-use", request_id),
                metadata={"state": "ACTIVE"},
            )
            return Actor(
                "device",
                device.id,
                None,
                project_ids,
                effective_scopes,
            )

    async def revoke(self, owner_id: UUID, device_id: UUID, *, request_id: UUID) -> None:
        now = self._now()
        async with self.session_factory() as session, session.begin():
            owner = await session.scalar(
                select(User).where(User.id == owner_id).with_for_update()
            )
            if (
                owner is None
                or owner.role != Role.OWNER
                or owner.status != UserStatus.ACTIVE
            ):
                raise InvalidDeviceGrant("Active OWNER is required")
            device = await session.scalar(
                select(DeviceConnection)
                .where(
                    DeviceConnection.id == device_id,
                    DeviceConnection.owner_id == owner.id,
                )
                .with_for_update()
            )
            if device is None:
                raise InvalidDeviceGrant("Device is not available")
            if device.revoked_at is not None:
                return
            sessions = list(
                await session.scalars(
                    select(DeviceSession)
                    .where(DeviceSession.device_id == device.id)
                    .with_for_update()
                )
            )
            device.revoked_at = now
            for device_session in sessions:
                if device_session.revoked_at is None:
                    device_session.revoked_at = now
            await self._audit(
                session,
                actor_kind="user",
                actor_id=owner.id,
                actor_role=Role.OWNER,
                action="device.revoke",
                object_type="device",
                object_id=device.id,
                outcome="SUCCESS",
                request_id=request_id,
                event_key=self._event_key("device-revoke", device.id),
                metadata={"state": "REVOKED"},
            )
