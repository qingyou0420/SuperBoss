"""Local password policy and rotating browser-session lifecycle."""

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from superboss.core.config import Settings
from superboss.core.errors import PasswordReuseForbiddenError
from superboss.core.security import (
    TokenError,
    decode_access_token,
    hash_token,
    issue_access_token,
    new_opaque_token,
    utcnow,
)
from superboss.modules.auth.models import AuthSession
from superboss.modules.auth.passwords import hash_password, verify_dummy_password, verify_password
from superboss.modules.auth.repository import AuthRepository
from superboss.modules.auth.schemas import SessionPair
from superboss.modules.users.models import User, UserStatus
from superboss.modules.users.repository import UserRepository

MAX_LOGIN_FAILURES = 5
LOGIN_LOCK_DURATION = timedelta(minutes=15)


class InvalidSession(Exception):
    """Session cannot be used."""


class LoginFailure(Exception):
    """Local credentials cannot issue a session."""

    def __init__(self, user: User | None, reason: str = "INVALID_CREDENTIALS") -> None:
        self.user = user
        self.reason = reason
        super().__init__("Local authentication failed")


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
        settings: Settings,
    ) -> None:
        self.session = session
        self.auth_repository = auth_repository
        self.user_repository = user_repository
        self.settings = settings

    async def login(self, username: str, password: str) -> CompletedLogin:
        user = await self.user_repository.by_username_for_update(username)
        if user is None:
            verify_dummy_password(password)
            raise LoginFailure(None)
        now = utcnow()
        if user.locked_until is not None and user.locked_until > now:
            verify_dummy_password(password)
            raise LoginFailure(user, "LOCKED")
        if user.locked_until is not None:
            user.locked_until = None
            user.failed_login_count = 0
        verification = verify_password(user.password_hash, password)
        if not verification.valid or user.status != UserStatus.ACTIVE:
            user.failed_login_count = min(user.failed_login_count + 1, MAX_LOGIN_FAILURES)
            if user.failed_login_count >= MAX_LOGIN_FAILURES:
                user.locked_until = now + LOGIN_LOCK_DURATION
            await self.session.flush()
            raise LoginFailure(user)
        if verification.needs_rehash:
            user.password_hash = hash_password(password)
        user.failed_login_count = 0
        user.locked_until = None
        user.last_login_at = now
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

    async def change_password(
        self, user_id: UUID, current_password: str, new_password: str
    ) -> CompletedLogin:
        user = await self.user_repository.by_id_for_update(user_id)
        if user is None or user.status != UserStatus.ACTIVE:
            raise LoginFailure(None)
        current = verify_password(user.password_hash, current_password)
        if not current.valid:
            raise LoginFailure(user)
        if verify_password(user.password_hash, new_password).valid:
            raise PasswordReuseForbiddenError()
        now = utcnow()
        user.password_hash = hash_password(new_password)
        user.password_changed_at = now
        user.must_change_password = False
        user.failed_login_count = 0
        user.locked_until = None
        await self.auth_repository.revoke_all_for_user(user.id, now)
        await self.session.flush()
        return CompletedLogin(await self.issue_session(user), user)

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
            token_role = str(claims["role"])
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
        if (
            user is None
            or user.status != UserStatus.ACTIVE
            or user.role.value != token_role
        ):
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
