"""Project application service."""

from __future__ import annotations

from datetime import timedelta
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from superboss.core.actors import (
    Actor,
    require_owner,
    require_project_actor,
)
from superboss.core.errors import ConflictError, DomainError, ForbiddenError, NotFoundError
from superboss.core.security import utcnow
from superboss.modules.audit.schemas import AuditEventInput
from superboss.modules.audit.service import AuditService
from superboss.modules.projects.models import (
    Project,
    ProjectMilestone,
    ProjectStage,
    ProjectStatus,
)
from superboss.modules.projects.schemas import MilestoneReplace, ProjectCreate, ProjectUpdate


def _loaded():
    return selectinload(Project.milestones)


def _sync_progress(project: Project) -> None:
    total = len(project.milestones)
    if total == 0:
        return
    done = sum(1 for item in project.milestones if item.done_at is not None)
    project.progress_percent = round(100 * done / total)


def _apply_stage(project: Project, stage: ProjectStage) -> None:
    project.stage = stage
    project.status = (
        ProjectStatus.ARCHIVED if stage is ProjectStage.ARCHIVED else ProjectStatus.ACTIVE
    )


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

    async def _require_owner(
        self, actor: Actor, action: str, request_id: UUID | None, project_id: UUID | None = None
    ) -> None:
        try:
            require_owner(actor)
        except ForbiddenError:
            await self._record(actor, action, "DENIED", request_id, project_id)
            raise

    async def _get(self, project_id: UUID) -> Project:
        project = cast(
            Project | None,
            await self.session.scalar(
                select(Project).options(_loaded()).where(Project.id == project_id)
            ),
        )
        if project is None:
            raise NotFoundError()
        return project

    async def create(
        self, actor: Actor, command: ProjectCreate, request_id: UUID | None = None
    ) -> Project:
        await self._require_owner(actor, "project.create", request_id)
        project = Project(
            name=command.name,
            description=command.description,
            is_test=command.is_test,
            starts_on=command.starts_on,
            due_on=command.due_on,
        )
        _apply_stage(project, command.stage)
        self.session.add(project)
        try:
            await self.session.flush()
        except IntegrityError as error:
            await self.session.rollback()
            raise ConflictError() from error
        await self.session.refresh(project, attribute_names=["milestones"])
        return project

    async def list(self, actor: Actor, request_id: UUID | None = None) -> list[Project]:
        try:
            require_project_actor(actor)
        except ForbiddenError:
            await self._record(actor, "project.list", "DENIED", request_id)
            raise
        statement = select(Project).options(_loaded()).order_by(Project.name)
        return list((await self.session.scalars(statement)).all())

    async def due_reminders(self, actor: Actor) -> list[dict[str, str]]:
        projects = await self.list(actor)
        today = utcnow().date()
        until = today + timedelta(days=3)
        messages: list[dict[str, str]] = []
        for project in projects:
            for milestone in project.milestones:
                if milestone.done_at is not None or milestone.due_on is None:
                    continue
                if not today <= milestone.due_on <= until:
                    continue
                days = (milestone.due_on - today).days
                when = "今天" if days == 0 else "明天" if days == 1 else f"{days} 天后"
                messages.append(
                    {"message": f"项目《{project.name}》里程碑「{milestone.title}」{when}到期"}
                )
        return messages

    async def get(self, actor: Actor, project_id: UUID, request_id: UUID | None = None) -> Project:
        try:
            require_project_actor(actor)
        except ForbiddenError:
            await self._record(actor, "project.read", "DENIED", request_id, project_id)
            raise
        return await self._get(project_id)

    async def update(
        self, actor: Actor, project_id: UUID, command: ProjectUpdate, request_id: UUID | None = None
    ) -> Project:
        await self._require_owner(actor, "project.update", request_id, project_id)
        project = await self._get(project_id)
        values = command.model_dump(exclude_unset=True)
        if not values:
            await self._record(actor, "project.update", "DENIED", request_id, project_id)
            raise DomainError("VALIDATION_ERROR", "Request validation failed", 422)
        if "name" in values and values["name"] is not None:
            project.name = values["name"]
        if "description" in values and values["description"] is not None:
            project.description = values["description"]
        if "starts_on" in values:
            project.starts_on = values["starts_on"]
        if "due_on" in values:
            project.due_on = values["due_on"]
        if "stage" in values and values["stage"] is not None:
            _apply_stage(project, values["stage"])
        if (
            "progress_percent" in values
            and values["progress_percent"] is not None
            and not project.milestones
        ):
            project.progress_percent = values["progress_percent"]
        try:
            await self.session.flush()
        except IntegrityError as error:
            await self.session.rollback()
            raise ConflictError() from error
        return project

    async def replace_milestones(
        self,
        actor: Actor,
        project_id: UUID,
        command: MilestoneReplace,
        request_id: UUID | None = None,
    ) -> Project:
        await self._require_owner(actor, "project.milestones.replace", request_id, project_id)
        project = await self._get(project_id)
        now = utcnow()
        project.milestones.clear()
        await self.session.flush()
        for index, item in enumerate(command.milestones):
            project.milestones.append(
                ProjectMilestone(
                    title=item.title,
                    due_on=item.due_on,
                    done_at=now if item.done else None,
                    sort_order=index,
                )
            )
        if project.milestones:
            _sync_progress(project)
        else:
            project.progress_percent = 0
        await self.session.flush()
        return project

    async def commit_and_record_success(
        self, actor: Actor, action: str, request_id: UUID, project_id: UUID | None = None
    ) -> None:
        """Close the business transaction before independently committing success evidence."""
        await self.session.commit()
        await self._record(actor, action, "SUCCESS", request_id, project_id)
