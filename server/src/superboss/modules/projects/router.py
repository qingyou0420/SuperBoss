"""Project API routes."""

from uuid import UUID

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from superboss.core.actors import Actor, get_actor
from superboss.core.db import get_session
from superboss.modules.audit.service import AuditService
from superboss.modules.projects.schemas import (
    MilestoneReplace,
    NodeComplete,
    ProjectCreate,
    ProjectRead,
    ProjectUpdate,
    ScheduleFromDate,
    ScheduleShift,
    TemplateWrite,
    WorkflowApply,
)
from superboss.modules.projects.service import ProjectService

router = APIRouter(prefix="/projects", tags=["projects"])


def get_service(request: Request, session: AsyncSession = Depends(get_session)) -> ProjectService:
    return ProjectService(session, AuditService(request.app.state.session_factory))


async def _project_read(service: ProjectService, actor: Actor, project: object) -> ProjectRead:
    from superboss.modules.projects.models import Project

    if isinstance(project, Project):
        return await service.serialize_for(actor, project)
    return ProjectRead.model_validate(project)


@router.post("", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
async def create_project(
    request: Request,
    command: ProjectCreate,
    actor: Actor = Depends(get_actor),
    service: ProjectService = Depends(get_service),
) -> ProjectRead:
    request_id = UUID(request.state.request_id)
    project = await service.create(actor, command, request_id)
    await service.commit_and_record_success(actor, "project.create", request_id, project.id)
    return await _project_read(service, actor, project)


@router.get("", response_model=list[ProjectRead])
async def list_projects(
    request: Request,
    actor: Actor = Depends(get_actor),
    service: ProjectService = Depends(get_service),
) -> list[ProjectRead]:
    request_id = UUID(request.state.request_id)
    projects = await service.list(actor, request_id)
    return [await _project_read(service, actor, project) for project in projects]


@router.get("/reminders")
async def list_reminders(
    request: Request,
    actor: Actor = Depends(get_actor),
    service: ProjectService = Depends(get_service),
) -> list[dict[str, str]]:
    del request
    return await service.due_reminders(actor)


@router.get("/workflow-templates")
async def list_workflow_templates(
    actor: Actor = Depends(get_actor),
    service: ProjectService = Depends(get_service),
) -> list[dict[str, object]]:
    return await service.list_templates(actor)


@router.put("/workflow-templates/default")
async def save_default_workflow_template(
    command: TemplateWrite,
    actor: Actor = Depends(get_actor),
    service: ProjectService = Depends(get_service),
) -> dict[str, object]:
    return await service.save_default_template(
        actor, command.name, [item.model_dump() for item in command.nodes]
    )


@router.get("/{project_id}", response_model=ProjectRead)
async def get_project(
    request: Request,
    project_id: UUID,
    actor: Actor = Depends(get_actor),
    service: ProjectService = Depends(get_service),
) -> ProjectRead:
    request_id = UUID(request.state.request_id)
    project = await service.get(actor, project_id, request_id)
    return await _project_read(service, actor, project)


@router.patch("/{project_id}", response_model=ProjectRead)
async def update_project(
    request: Request,
    project_id: UUID,
    command: ProjectUpdate,
    actor: Actor = Depends(get_actor),
    service: ProjectService = Depends(get_service),
) -> ProjectRead:
    request_id = UUID(request.state.request_id)
    project = await service.update(actor, project_id, command, request_id)
    await service.commit_and_record_success(actor, "project.update", request_id, project_id)
    return await _project_read(service, actor, project)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    request: Request,
    project_id: UUID,
    actor: Actor = Depends(get_actor),
    service: ProjectService = Depends(get_service),
) -> None:
    request_id = UUID(request.state.request_id)
    await service.delete(actor, project_id, request_id)
    await service.commit_and_record_success(
        actor, "project.delete", request_id, object_id=project_id
    )


@router.post("/{project_id}/schedule-preview")
async def preview_schedule(
    request: Request,
    project_id: UUID,
    command: ScheduleShift,
    actor: Actor = Depends(get_actor),
    service: ProjectService = Depends(get_service),
) -> dict[str, object]:
    project = await service.get(actor, project_id, UUID(request.state.request_id))
    return service.preview_shift(project, command.node_id, command.days)


@router.post("/{project_id}/schedule", response_model=ProjectRead)
async def apply_schedule(
    request: Request,
    project_id: UUID,
    command: ScheduleShift,
    actor: Actor = Depends(get_actor),
    service: ProjectService = Depends(get_service),
) -> ProjectRead:
    request_id = UUID(request.state.request_id)
    project = await service.apply_shift(
        actor, project_id, command.node_id, command.days, command.reason
    )
    await service.commit_and_record_success(actor, "project.schedule", request_id, project_id)
    return await _project_read(service, actor, project)


@router.post("/{project_id}/apply-workflow", response_model=ProjectRead)
async def apply_workflow(
    request: Request,
    project_id: UUID,
    command: WorkflowApply,
    actor: Actor = Depends(get_actor),
    service: ProjectService = Depends(get_service),
) -> ProjectRead:
    request_id = UUID(request.state.request_id)
    project = await service.apply_workflow(
        actor,
        project_id,
        starts_on=command.starts_on,
        template_version_id=command.template_version_id,
    )
    await service.commit_and_record_success(actor, "project.workflow.apply", request_id, project_id)
    return await _project_read(service, actor, project)


@router.post("/{project_id}/schedule-from-date", response_model=ProjectRead)
async def schedule_from_date(
    request: Request,
    project_id: UUID,
    command: ScheduleFromDate,
    actor: Actor = Depends(get_actor),
    service: ProjectService = Depends(get_service),
) -> ProjectRead:
    request_id = UUID(request.state.request_id)
    project = await service.reschedule_from_date(
        actor, project_id, command.node_id, command.new_start, command.reason
    )
    await service.commit_and_record_success(
        actor, "project.schedule.key_date", request_id, project_id
    )
    return await _project_read(service, actor, project)


@router.post("/{project_id}/complete-service", response_model=ProjectRead)
async def complete_service(
    request: Request,
    project_id: UUID,
    actor: Actor = Depends(get_actor),
    service: ProjectService = Depends(get_service),
) -> ProjectRead:
    request_id = UUID(request.state.request_id)
    project = await service.complete_service(actor, project_id)
    await service.commit_and_record_success(
        actor, "project.service.complete", request_id, project_id
    )
    return await _project_read(service, actor, project)


@router.post("/{project_id}/nodes/{node_id}/skip", response_model=ProjectRead)
async def skip_node(
    request: Request,
    project_id: UUID,
    node_id: UUID,
    actor: Actor = Depends(get_actor),
    service: ProjectService = Depends(get_service),
) -> ProjectRead:
    request_id = UUID(request.state.request_id)
    project = await service.skip_node(actor, project_id, node_id)
    await service.commit_and_record_success(actor, "project.node.skip", request_id, project_id)
    return await _project_read(service, actor, project)


@router.post("/{project_id}/nodes/{node_id}/complete", response_model=ProjectRead)
async def complete_node(
    request: Request,
    project_id: UUID,
    node_id: UUID,
    command: NodeComplete,
    actor: Actor = Depends(get_actor),
    service: ProjectService = Depends(get_service),
) -> ProjectRead:
    request_id = UUID(request.state.request_id)
    project = await service.complete_node(actor, project_id, node_id, command.evidence)
    await service.commit_and_record_success(actor, "project.node.complete", request_id, project_id)
    return await _project_read(service, actor, project)


@router.put("/{project_id}/milestones", response_model=ProjectRead)
async def replace_milestones(
    request: Request,
    project_id: UUID,
    command: MilestoneReplace,
    actor: Actor = Depends(get_actor),
    service: ProjectService = Depends(get_service),
) -> ProjectRead:
    request_id = UUID(request.state.request_id)
    project = await service.replace_milestones(actor, project_id, command, request_id)
    await service.commit_and_record_success(
        actor, "project.milestones.replace", request_id, project_id
    )
    return await _project_read(service, actor, project)
