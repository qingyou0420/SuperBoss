"""Project API routes."""

from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from superboss.core.actors import Actor, get_actor
from superboss.modules.audit.service import AuditService
from superboss.modules.projects.repository import ProjectRepository
from superboss.modules.projects.schemas import ProjectCreate, ProjectRead
from superboss.modules.projects.service import ProjectService

router = APIRouter(prefix="/projects", tags=["projects"])


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    session = request.app.state.session_factory()
    try:
        yield session
    except Exception:
        await session.rollback()
        raise
    else:
        await session.commit()
    finally:
        await session.close()


def get_service(request: Request, session: AsyncSession = Depends(get_session)) -> ProjectService:
    return ProjectService(ProjectRepository(session), AuditService(request.app.state.session_factory))


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
    return ProjectRead.model_validate(project)


@router.get("", response_model=list[ProjectRead])
async def list_projects(
    request: Request, actor: Actor = Depends(get_actor), service: ProjectService = Depends(get_service)
) -> list[ProjectRead]:
    request_id = UUID(request.state.request_id)
    projects = await service.list(actor, request_id)
    await service.commit_and_record_success(actor, "project.list", request_id)
    return [ProjectRead.model_validate(project) for project in projects]


@router.get("/{project_id}", response_model=ProjectRead)
async def get_project(
    request: Request,
    project_id: UUID,
    actor: Actor = Depends(get_actor),
    service: ProjectService = Depends(get_service),
) -> ProjectRead:
    request_id = UUID(request.state.request_id)
    project = await service.get(actor, project_id, request_id)
    await service.commit_and_record_success(actor, "project.read", request_id, project_id)
    return ProjectRead.model_validate(project)
