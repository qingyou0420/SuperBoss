"""Authentication policy and rotating session lifecycle."""

from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from superboss.core.config import Settings
from superboss.core.security import (
    TokenError,
    decode_access_token,
    hash_token,
    issue_access_token,
    new_opaque_token,
    utcnow,
)
from superboss.infrastructure.wecom import WeComIdentity
from superboss.modules.auth.models import AuthSession
from superboss.modules.auth.repository import AuthRepository
from superboss.modules.auth.schemas import SessionPair
from superboss.modules.users.models import Role, User, UserStatus
from superboss.modules.users.repository import UserRepository


class IdentityProvider(Protocol):
    async def exchange_code(self, code: str) -> WeComIdentity: ...


class InvalidSession(Exception):
    """Session cannot be used."""


class ForbiddenIdentity(Exception):
    """WeCom identity is not permitted to log in."""


@dataclass(frozen=True)
class CompletedLogin:
    pair: SessionPair
    user: User


class AuthService:
    def __init__(
        self,
        session: AsyncSession,
        auth_repository: AuthRepository,
        user_repository: UserRepository,
        provider: IdentityProvider | None,
        settings: Settings,
    ) -> None:
        self.session = session
        self.auth_repository = auth_repository
        self.user_repository = user_repository
        self.provider = provider
        self.settings = settings

    async def complete_wecom_login(self, code: str, state: str) -> CompletedLogin:
        del state  # State is verified at the HTTP boundary before identity exchange.
        if self.provider is None:
            raise InvalidSession("Identity provider is unavailable")
        identity = await self.provider.exchange_code(code)
        user = await self.user_repository.by_wecom_userid(identity.userid)
        if user is None and identity.userid == self.settings.owner_wecom_userid:
            user = User(
                wecom_userid=identity.userid,
                display_name="",
                role=Role.OWNER,
                status=UserStatus.ACTIVE,
            )
            await self.user_repository.add(user)
        if user is None or user.status != UserStatus.ACTIVE:
            raise ForbiddenIdentity("Identity is not authorized")
        user.last_login_at = utcnow()
        await self.session.flush()
        return CompletedLogin(await self.issue_session(user), user)

    async def issue_session(self, user: User) -> SessionPair:
        raw_refresh = new_opaque_token()
        now = utcnow()
        refresh_expires_at = now + timedelta(days=14)
        auth_session = AuthSession(
            user_id=user.id,
            access_jti="pending",
            refresh_token_hash=hash_token(raw_refresh),
            access_expires_at=now,
            refresh_expires_at=refresh_expires_at,
        )
        await self.auth_repository.add(auth_session)
        access_token, access_expires_at = issue_access_token(
            self.settings, user.id, str(user.role), auth_session.id
        )
        claims = decode_access_token(self.settings, access_token)
        auth_session.access_jti = str(claims["jti"])
        auth_session.access_expires_at = access_expires_at
        await self.session.flush()
        return SessionPair(access_token, raw_refresh, access_expires_at, refresh_expires_at)

    async def rotate_refresh_token(self, raw_token: str) -> SessionPair:
        current = await self.auth_repository.by_refresh_hash(hash_token(raw_token))
        now = utcnow()
        if (
            current is None
            or current.revoked_at is not None
            or current.refresh_used_at is not None
            or current.refresh_expires_at <= now
        ):
            raise InvalidSession("Refresh token is invalid")
        current.refresh_used_at = now
        current.revoked_at = now
        await self.session.flush()
        user = await self.session.get(User, current.user_id)
        if user is None or user.status != UserStatus.ACTIVE:
            raise InvalidSession("Refresh token is invalid")
        return await self.issue_session(user)

    async def authenticate_access_token(self, raw_token: str) -> User:
        try:
            claims = decode_access_token(self.settings, raw_token)
            session_id = UUID(str(claims["session_id"]))
            user_id = UUID(str(claims["sub"]))
            token_role = Role(str(claims["role"]))
        except (TokenError, ValueError) as error:
            raise InvalidSession("Access token is invalid") from error
        auth_session = await self.auth_repository.by_id(session_id)
        if (
            auth_session is None
            or auth_session.revoked_at is not None
            or auth_session.access_expires_at <= utcnow()
            or auth_session.access_jti != claims["jti"]
            or auth_session.user_id != user_id
        ):
            raise InvalidSession("Access token is invalid")
        user = await self.session.get(User, user_id)
        if user is None or user.status != UserStatus.ACTIVE or user.role != token_role:
            raise InvalidSession("Access token is invalid")
        return user

    async def logout(self, access_token: str | None, refresh_token: str | None) -> None:
        records: list[AuthSession] = []
        if refresh_token:
            found = await self.auth_repository.by_refresh_hash(hash_token(refresh_token))
            if found is not None:
                records.append(found)
        if access_token:
            try:
                claims = decode_access_token(self.settings, access_token)
                found = await self.auth_repository.by_id(UUID(str(claims["session_id"])))
                if found is not None:
                    records.append(found)
            except (TokenError, ValueError):
                pass
        for record in records:
            await self.auth_repository.revoke(record, utcnow())
