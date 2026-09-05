from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Path, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from superboss.core.actors import Actor, get_actor
from superboss.core.errors import DomainError
from superboss.modules.audit.schemas import AuditEventInput
from superboss.modules.audit.service import AuditService
from superboss.modules.files.models import File
from superboss.modules.files.schemas import (
    FilePatch,
    FileRead,
    FolderCreate,
    FolderRead,
    UploadComplete,
    UploadStart,
)
from superboss.modules.files.service import FileService
from superboss.modules.files.storage import CompletedPart

router = APIRouter(prefix="/files", tags=["files"])
folders_router = APIRouter(prefix="/folders", tags=["folders"])

_AUDITABLE_UPLOAD_DENIAL_CODES = frozenset(
    {
        "FOLDER_FORBIDDEN",
        "FOLDER_NOT_FOUND",
        "FILE_UPLOAD_CONFLICT",
        "FILE_UPLOAD_NOT_FOUND",
        "FILE_UPLOAD_NOT_ACTIVE",
    }
)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    session = request.app.state.session_factory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


def get_service(request: Request, session: AsyncSession = Depends(get_session)) -> FileService:
    return FileService(
        session, request.app.state.object_storage, request.app.state.enqueue_file_scan
    )


async def _record_download_audit(
    request: Request,
    actor: Actor,
    file: File | None,
    file_id: UUID,
    outcome: str,
    *,
    best_effort: bool,
) -> None:
    event = AuditEventInput(
        actor=actor,
        action="file.download",
        object_type="file",
        object_id=file.id if file is not None else file_id,
        project_id=file.project_id if file is not None else None,
        outcome=outcome,
        request_id=UUID(request.state.request_id),
        metadata={"state": file.state.value} if file is not None else {},
    )
    if not best_effort:
        await AuditService(request.app.state.session_factory).record(event)
        return
    try:
        await AuditService(request.app.state.session_factory).record(event)
    except Exception:  # noqa: BLE001 -- audit unavailability must not alter download authorization
        return


async def _record_upload_denial_audit(
    request: Request,
    actor: Actor,
    *,
    action: str,
    object_type: str,
    object_id: UUID,
    project_id: UUID | None,
    error: DomainError,
) -> None:
    """Persist safe evidence for authenticated upload probes without changing the denial."""
    if error.code not in _AUDITABLE_UPLOAD_DENIAL_CODES:
        return
    event = AuditEventInput(
        actor=actor,
        action=action,
        object_type=object_type,
        object_id=object_id,
        project_id=project_id,
        outcome="DENIED",
        request_id=UUID(request.state.request_id),
        metadata={"error_code": error.code},
    )
    try:
        await AuditService(request.app.state.session_factory).record(event)
    except Exception:  # noqa: BLE001 -- audit unavailability must not alter authorization
        return


async def _upload_project_id(session: AsyncSession, upload_id: UUID) -> UUID | None:
    file = await session.get(File, upload_id)
    return file.project_id if file is not None else None


@router.post("/uploads", status_code=status.HTTP_201_CREATED)
async def start(
    request: Request,
    command: UploadStart,
    idempotency_key: str = Header(alias="Idempotency-Key", pattern=r"^[!-~]{1,255}$"),
    actor: Actor = Depends(get_actor),
    service: FileService = Depends(get_service),
) -> dict[str, str]:
    try:
        file = await service.start_upload(actor, command, idempotency_key)
    except DomainError as error:
        await _record_upload_denial_audit(
            request,
            actor,
            action="file.upload.start",
            object_type="folder",
            object_id=command.folder_id,
            project_id=command.project_id,
            error=error,
        )
        raise
    return {"upload_id": str(file.id), "file_id": str(file.id)}


@router.post("/uploads/{upload_id}/complete")
async def complete(
    request: Request,
    upload_id: UUID,
    command: UploadComplete,
    actor: Actor = Depends(get_actor),
    service: FileService = Depends(get_service),
) -> dict[str, str]:
    try:
        file = await service.complete_upload(
            actor,
            upload_id,
            [CompletedPart(p.part_number, p.etag) for p in command.parts],
            UUID(request.state.request_id),
        )
    except DomainError as error:
        await _record_upload_denial_audit(
            request,
            actor,
            action="file.upload.complete",
            object_type="upload",
            object_id=upload_id,
            project_id=await _upload_project_id(service.session, upload_id),
            error=error,
        )
        raise
    await service.session.commit()
    return {"file_id": str(file.id), "state": file.state}


@router.post("/uploads/{upload_id}/parts/{part_number}")
async def part(
    request: Request,
    upload_id: UUID,
    part_number: int = Path(ge=1, le=10000),
    actor: Actor = Depends(get_actor),
    service: FileService = Depends(get_service),
) -> dict[str, str]:
    try:
        url = await service.presign_part(actor, upload_id, part_number)
    except DomainError as error:
        await _record_upload_denial_audit(
            request,
            actor,
            action="file.upload.part_url",
            object_type="upload",
            object_id=upload_id,
            project_id=await _upload_project_id(service.session, upload_id),
            error=error,
        )
        raise
    return {"url": url}


@router.get("/{file_id}/download")
async def download(
    request: Request,
    file_id: UUID,
    actor: Actor = Depends(get_actor),
    service: FileService = Depends(get_service),
) -> dict[str, str]:
    try:
        url = await service.presign_download(actor, file_id)
    except DomainError:
        file = await service.session.get(File, file_id)
        await _record_download_audit(request, actor, file, file_id, "DENIED", best_effort=True)
        raise
    file = await service.session.get(File, file_id)
    assert file is not None
    await _record_download_audit(request, actor, file, file_id, "SUCCESS", best_effort=False)
    return {"url": url}


@router.get("", response_model=list[FileRead])
async def list_files(
    folder_id: UUID,
    actor: Actor = Depends(get_actor),
    service: FileService = Depends(get_service),
) -> list[FileRead]:
    return [FileRead.model_validate(item) for item in await service.list_files(actor, folder_id)]


@router.patch("/{file_id}", response_model=FileRead)
async def patch_file(
    file_id: UUID,
    command: FilePatch,
    actor: Actor = Depends(get_actor),
    service: FileService = Depends(get_service),
) -> FileRead:
    return FileRead.model_validate(await service.patch_file(actor, file_id, command))


@router.delete("/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_file(
    request: Request,
    file_id: UUID,
    actor: Actor = Depends(get_actor),
    service: FileService = Depends(get_service),
) -> None:
    file = await service.session.get(File, file_id)
    await service.delete_file(actor, file_id)
    await AuditService(request.app.state.session_factory).record(
        AuditEventInput(
            actor=actor,
            action="file.delete",
            object_type="file",
            object_id=file_id,
            project_id=file.project_id if file is not None else None,
            outcome="SUCCESS",
            request_id=UUID(request.state.request_id),
            metadata={"filename": file.filename} if file is not None else {},
        )
    )


@folders_router.get("", response_model=list[FolderRead])
async def list_folders(
    actor: Actor = Depends(get_actor),
    service: FileService = Depends(get_service),
) -> list[FolderRead]:
    return [FolderRead.model_validate(item) for item in await service.list_folders(actor)]


@folders_router.post("", response_model=FolderRead, status_code=status.HTTP_201_CREATED)
async def create_folder(
    command: FolderCreate,
    actor: Actor = Depends(get_actor),
    service: FileService = Depends(get_service),
) -> FolderRead:
    return FolderRead.model_validate(await service.create_folder(actor, command))
