"""Directory routes."""

from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from superboss.core.actors import Actor, get_actor
from superboss.core.db import get_session
from superboss.modules.directory.schemas import (
    CommunicationCreate,
    CommunicationRead,
    ConvertProjectCreate,
    DirectoryConflictListRead,
    DirectoryConflictRead,
    DirectoryConflictResolve,
    DirectoryEntryRead,
    DirectoryFacets,
    DirectoryListRead,
    DirectoryProjectLinkRead,
    ImportResult,
)
from superboss.modules.directory.service import DirectoryService, to_read
from superboss.modules.projects.schemas import ProjectRead

router = APIRouter(prefix="/directory", tags=["directory"])


def get_service(session: AsyncSession = Depends(get_session)) -> DirectoryService:
    return DirectoryService(session)


@router.post("/imports", response_model=ImportResult)
async def import_directory(
    actor: Actor = Depends(get_actor),
    service: DirectoryService = Depends(get_service),
    file: UploadFile = File(...),
) -> ImportResult:
    payload = await file.read()
    filename = file.filename or "upload.xlsx"
    result = await service.import_workbook(actor, filename, payload)
    return ImportResult(
        inserted=int(result["inserted"]),
        updated=int(result["updated"]),
        skipped=int(result["skipped"]),
        source_filename=filename[:255],
        conflicts=list(result.get("conflicts") or []),
    )


@router.get("/conflicts", response_model=DirectoryConflictListRead)
async def list_directory_conflicts(
    actor: Actor = Depends(get_actor),
    service: DirectoryService = Depends(get_service),
    status: str | None = Query(default="OPEN", max_length=16),
    offset: int = Query(default=0, ge=0, le=100_000),
    limit: int = Query(default=50, ge=1, le=500),
) -> DirectoryConflictListRead:
    rows, total = await service.list_conflicts(
        actor, status=status or None, offset=offset, limit=limit
    )
    return DirectoryConflictListRead(
        items=[DirectoryConflictRead.model_validate(item) for item in rows],
        total=total,
    )


@router.post("/conflicts/{conflict_id}/resolve", response_model=DirectoryConflictRead)
async def resolve_directory_conflict(
    conflict_id: UUID,
    command: DirectoryConflictResolve,
    actor: Actor = Depends(get_actor),
    service: DirectoryService = Depends(get_service),
) -> DirectoryConflictRead:
    row = await service.resolve_conflict(actor, conflict_id, command.action)
    return DirectoryConflictRead.model_validate(row)


@router.get("/facets", response_model=DirectoryFacets)
async def directory_facets(
    actor: Actor = Depends(get_actor),
    service: DirectoryService = Depends(get_service),
) -> DirectoryFacets:
    payload = await service.facets(actor)
    return DirectoryFacets.model_validate(payload)


@router.get("", response_model=DirectoryListRead)
async def list_directory(
    actor: Actor = Depends(get_actor),
    service: DirectoryService = Depends(get_service),
    q: str | None = Query(default=None, max_length=80),
    district: str | None = Query(default=None, max_length=64),
    street: str | None = Query(default=None, max_length=64),
    offset: int = Query(default=0, ge=0, le=100_000),
    limit: int = Query(default=50, ge=1, le=200),
) -> DirectoryListRead:
    rows, total = await service.list_entries(
        actor, query=q, district=district, street=street, offset=offset, limit=limit
    )
    return DirectoryListRead(
        items=[DirectoryEntryRead.model_validate(to_read(item, actor)) for item in rows],
        total=total,
    )


@router.get("/{entry_id}", response_model=DirectoryEntryRead)
async def get_directory_entry(
    entry_id: UUID,
    actor: Actor = Depends(get_actor),
    service: DirectoryService = Depends(get_service),
) -> DirectoryEntryRead:
    entry = await service.get_entry(actor, entry_id)
    return DirectoryEntryRead.model_validate(to_read(entry, actor))


@router.get("/{entry_id}/communications", response_model=list[CommunicationRead])
async def list_communications(
    entry_id: UUID,
    actor: Actor = Depends(get_actor),
    service: DirectoryService = Depends(get_service),
) -> list[CommunicationRead]:
    rows = await service.list_communications(actor, entry_id)
    return [CommunicationRead.model_validate(item) for item in rows]


@router.post(
    "/{entry_id}/communications",
    response_model=CommunicationRead,
    status_code=status.HTTP_201_CREATED,
)
async def add_communication(
    entry_id: UUID,
    command: CommunicationCreate,
    actor: Actor = Depends(get_actor),
    service: DirectoryService = Depends(get_service),
) -> CommunicationRead:
    row = await service.add_communication(actor, entry_id, command)
    return CommunicationRead.model_validate(row)


@router.get("/{entry_id}/projects", response_model=list[DirectoryProjectLinkRead])
async def list_directory_projects(
    entry_id: UUID,
    actor: Actor = Depends(get_actor),
    service: DirectoryService = Depends(get_service),
) -> list[DirectoryProjectLinkRead]:
    rows = await service.list_projects(actor, entry_id)
    return [DirectoryProjectLinkRead.model_validate(item) for item in rows]


@router.post(
    "/{entry_id}/convert-project",
    response_model=ProjectRead,
    status_code=status.HTTP_201_CREATED,
)
async def convert_to_project(
    entry_id: UUID,
    command: ConvertProjectCreate,
    actor: Actor = Depends(get_actor),
    service: DirectoryService = Depends(get_service),
) -> ProjectRead:
    project = await service.convert_to_project(actor, entry_id, command)
    return ProjectRead.model_validate(project)
