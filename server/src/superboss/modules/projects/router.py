"""Project API routes."""

from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from superboss.core.actors import Actor, get_actor
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


def get_service(session: AsyncSession = Depends(get_session)) -> ProjectService:
    return ProjectService(ProjectRepository(session))


@router.post("", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
async def create_project(
    command: ProjectCreate,
    actor: Actor = Depends(get_actor),
    service: ProjectService = Depends(get_service),
) -> ProjectRead:
    return ProjectRead.model_validate(await service.create(actor, command))


@router.get("", response_model=list[ProjectRead])
async def list_projects(
    actor: Actor = Depends(get_actor), service: ProjectService = Depends(get_service)
) -> list[ProjectRead]:
    return [ProjectRead.model_validate(project) for project in await service.list(actor)]


@router.get("/{project_id}", response_model=ProjectRead)
async def get_project(
    project_id: UUID,
    actor: Actor = Depends(get_actor),
    service: ProjectService = Depends(get_service),
) -> ProjectRead:
    return ProjectRead.model_validate(await service.get(actor, project_id))
