"""Project persistence queries."""

from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from superboss.modules.projects.models import Project, ProjectMember


class ProjectRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, project: Project) -> None:
        self.session.add(project)
        await self.session.flush()

    async def list_all(self) -> list[Project]:
        return list((await self.session.scalars(select(Project).order_by(Project.name))).all())

    async def list_for_staff(self, user_id: UUID) -> list[Project]:
        """Fetch only memberships in SQL; never filter a full project set in Python."""
        statement = (
            select(Project)
            .join(ProjectMember, ProjectMember.project_id == Project.id)
            .where(ProjectMember.user_id == user_id)
            .order_by(Project.name)
        )
        return list((await self.session.scalars(statement)).all())

    async def by_id(self, project_id: UUID) -> Project | None:
        return cast(Project | None, await self.session.get(Project, project_id))
