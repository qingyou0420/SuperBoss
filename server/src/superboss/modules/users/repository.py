"""User persistence rules which protect the sole OWNER."""

from typing import cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from superboss.modules.users.models import Role, User, UserStatus


class ProtectedOwnerError(Exception):
    """A service attempted to mutate the protected OWNER account."""


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def by_wecom_userid(self, userid: str) -> User | None:
        return cast(
            User | None, await self.session.scalar(select(User).where(User.wecom_userid == userid))
        )

    async def add(self, user: User) -> None:
        self.session.add(user)
        await self.session.flush()

    async def disable(self, user: User) -> None:
        self._ensure_not_owner(user)
        user.status = UserStatus.DISABLED
        await self.session.flush()

    async def change_role(self, user: User, role: Role) -> None:
        self._ensure_not_owner(user)
        user.role = role
        await self.session.flush()

    async def delete(self, user: User) -> None:
        self._ensure_not_owner(user)
        await self.session.delete(user)
        await self.session.flush()

    @staticmethod
    def _ensure_not_owner(user: User) -> None:
        if user.role == Role.OWNER:
            raise ProtectedOwnerError("The OWNER account is immutable")
