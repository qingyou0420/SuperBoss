"""Project application service."""

from uuid import UUID

from sqlalchemy.exc import IntegrityError

from superboss.core.actors import (
    Actor,
    require_owner,
    require_project_access,
    require_project_actor,
)
from superboss.core.errors import ConflictError, ForbiddenError, NotFoundError
from superboss.modules.audit.schemas import AuditEventInput
from superboss.modules.audit.service import AuditService
from superboss.modules.projects.models import Project
from superboss.modules.projects.repository import ProjectRepository
from superboss.modules.projects.schemas import ProjectCreate
from superboss.modules.users.models import Role


class ProjectService:
    def __init__(self, repository: ProjectRepository, audit_service: AuditService | None = None) -> None:
        self.repository = repository
        self.audit_service = audit_service

    async def _record(
        self,
        actor: Actor,
        action: str,
        outcome: str,
        request_id: UUID | None = None,
        project_id: UUID | None = None,
    ) -> None:
        if self.audit_service is None or request_id is None:
            return
        await self.audit_service.record(
            AuditEventInput(
                actor=actor,
                action=action,
                object_type="project",
                object_id=project_id,
                project_id=project_id,
                outcome=outcome,
                request_id=request_id,
            )
        )

    async def create(self, actor: Actor, command: ProjectCreate, request_id: UUID | None = None) -> Project:
        try:
            require_owner(actor)
        except ForbiddenError:
            await self._record(actor, "project.create", "DENIED", request_id)
            raise
        project = Project(name=command.name, is_test=command.is_test)
        try:
            await self.repository.create(project)
            await self.repository.session.commit()
        except IntegrityError as error:
            await self.repository.session.rollback()
            raise ConflictError() from error
        await self._record(actor, "project.create", "SUCCESS", request_id, project.id)
        return project

    async def list(self, actor: Actor, request_id: UUID | None = None) -> list[Project]:
        try:
            require_project_actor(actor)
        except ForbiddenError:
            await self._record(actor, "project.list", "DENIED", request_id)
            raise
        if actor.role == Role.OWNER:
            projects = await self.repository.list_all()
        else:
            projects = await self.repository.list_for_staff(actor.subject_id)
        await self._record(actor, "project.list", "SUCCESS", request_id)
        return projects

    async def get(self, actor: Actor, project_id: UUID, request_id: UUID | None = None) -> Project:
        try:
            require_project_actor(actor)
        except ForbiddenError:
            await self._record(actor, "project.read", "DENIED", request_id, project_id)
            raise
        project = await self.repository.by_id(project_id)
        if project is None:
            raise NotFoundError()
        try:
            require_project_access(actor, project_id)
        except ForbiddenError:
            await self._record(actor, "project.read", "DENIED", request_id, project_id)
            raise
        await self._record(actor, "project.read", "SUCCESS", request_id, project_id)
        return project
