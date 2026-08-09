"""Project application service."""

from uuid import UUID

from sqlalchemy.exc import IntegrityError

from superboss.core.actors import Actor, require_owner, require_project_access
from superboss.core.errors import ConflictError, NotFoundError
from superboss.modules.projects.models import Project
from superboss.modules.projects.repository import ProjectRepository
from superboss.modules.projects.schemas import ProjectCreate
from superboss.modules.users.models import Role


class ProjectService:
    def __init__(self, repository: ProjectRepository) -> None:
        self.repository = repository

    async def create(self, actor: Actor, command: ProjectCreate) -> Project:
        require_owner(actor)
        project = Project(name=command.name, is_test=command.is_test)
        try:
            await self.repository.create(project)
        except IntegrityError as error:
            await self.repository.session.rollback()
            raise ConflictError() from error
        return project

    async def list(self, actor: Actor) -> list[Project]:
        if actor.role == Role.OWNER:
            return await self.repository.list_all()
        return await self.repository.list_for_staff(actor.subject_id)

    async def get(self, actor: Actor, project_id: UUID) -> Project:
        project = await self.repository.by_id(project_id)
        if project is None:
            raise NotFoundError()
        require_project_access(actor, project_id)
        return project
