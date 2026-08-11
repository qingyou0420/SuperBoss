"""User persistence rules which protect the sole OWNER."""

from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from superboss.modules.auth.models import AuthSession
from superboss.modules.projects.models import Project, ProjectMember
from superboss.modules.users.models import Role, User, UserStatus


class ProtectedOwnerError(Exception):
    """A service attempted to mutate the protected OWNER account."""


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def by_username(self, username: str) -> User | None:
        return cast(
            User | None, await self.session.scalar(select(User).where(User.username == username))
        )

    async def by_username_for_update(self, username: str) -> User | None:
        return cast(
            User | None,
            await self.session.scalar(
                select(User).where(User.username == username).with_for_update()
            ),
        )

    async def by_id_for_update(self, user_id: UUID) -> User | None:
        return cast(
            User | None,
            await self.session.scalar(select(User).where(User.id == user_id).with_for_update()),
        )

    async def list_all(self) -> list[User]:
        return list((await self.session.scalars(select(User).order_by(User.username))).all())

    async def projects_for_user(self, user_id: UUID) -> list[Project]:
        statement = (
            select(Project)
            .join(ProjectMember, ProjectMember.project_id == Project.id)
            .where(ProjectMember.user_id == user_id)
            .order_by(Project.name, Project.id)
        )
        return list((await self.session.scalars(statement)).all())

    async def projects_for_update(self, project_ids: list[UUID]) -> list[Project]:
        if not project_ids:
            return []
        statement = (
            select(Project)
            .where(Project.id.in_(project_ids))
            .order_by(Project.id)
            .with_for_update()
        )
        return list((await self.session.scalars(statement)).all())

    async def replace_project_memberships(self, user_id: UUID, project_ids: list[UUID]) -> None:
        await self.session.execute(
            delete(ProjectMember).where(ProjectMember.user_id == user_id)
        )
        self.session.add_all(
            [ProjectMember(user_id=user_id, project_id=project_id) for project_id in project_ids]
        )
        await self.session.flush()

    async def revoke_browser_sessions(self, user_id: UUID, at: datetime) -> None:
        await self.session.execute(
            update(AuthSession)
            .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
            .values(revoked_at=at)
        )
        await self.session.flush()

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
