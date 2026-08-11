"""Session persistence operations."""

from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from superboss.modules.auth.models import AuthSession


class AuthRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def by_refresh_hash(self, token_hash: str) -> AuthSession | None:
        return cast(
            AuthSession | None,
            await self.session.scalar(
                select(AuthSession)
                .where(AuthSession.refresh_token_hash == token_hash)
                .with_for_update()
            ),
        )

    async def by_id(self, session_id: UUID) -> AuthSession | None:
        return await self.session.get(AuthSession, session_id)

    async def add(self, auth_session: AuthSession) -> None:
        self.session.add(auth_session)
        await self.session.flush()

    async def revoke(self, auth_session: AuthSession, at: datetime) -> None:
        auth_session.revoked_at = at
        await self.session.flush()

    async def revoke_all_for_user(self, user_id: UUID, at: datetime) -> None:
        await self.session.execute(
            update(AuthSession)
            .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
            .values(revoked_at=at)
        )
        await self.session.flush()
