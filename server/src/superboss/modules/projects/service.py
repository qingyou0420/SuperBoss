"""Project application service."""

from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from superboss.core.actors import (
    Actor,
    require_owner,
    require_project_access,
    require_project_actor,
)
from superboss.core.errors import ConflictError, ForbiddenError, NotFoundError
from superboss.modules.audit.schemas import AuditEventInput
from superboss.modules.audit.service import AuditService
from superboss.modules.projects.models import Project, ProjectMember
from superboss.modules.projects.schemas import ProjectCreate
from superboss.modules.users.models import Role


class ProjectService:
    def __init__(self, session: AsyncSession, audit_service: AuditService | None = None) -> None:
        self.session = session
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
        self.session.add(project)
        try:
            await self.session.flush()
        except IntegrityError as error:
            await self.session.rollback()
            raise ConflictError() from error
        return project

    async def list(self, actor: Actor, request_id: UUID | None = None) -> list[Project]:
        try:
            require_project_actor(actor)
        except ForbiddenError:
            await self._record(actor, "project.list", "DENIED", request_id)
            raise
        if actor.role == Role.OWNER:
            return list((await self.session.scalars(select(Project).order_by(Project.name))).all())
        statement = (
            select(Project)
            .join(ProjectMember, ProjectMember.project_id == Project.id)
            .where(ProjectMember.user_id == actor.subject_id)
            .order_by(Project.name)
        )
        return list((await self.session.scalars(statement)).all())

    async def get(self, actor: Actor, project_id: UUID, request_id: UUID | None = None) -> Project:
        try:
            require_project_actor(actor)
        except ForbiddenError:
            await self._record(actor, "project.read", "DENIED", request_id, project_id)
            raise
        project = cast(Project | None, await self.session.get(Project, project_id))
        if project is None:
            raise NotFoundError()
        try:
            require_project_access(actor, project_id)
        except ForbiddenError:
            await self._record(actor, "project.read", "DENIED", request_id, project_id)
            raise
        return project

    async def commit_and_record_success(
        self, actor: Actor, action: str, request_id: UUID, project_id: UUID | None = None
    ) -> None:
        """Close the business transaction before independently committing success evidence."""
        await self.session.commit()
        await self._record(actor, action, "SUCCESS", request_id, project_id)
