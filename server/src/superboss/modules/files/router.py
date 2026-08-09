from collections.abc import AsyncIterator
from inspect import isawaitable
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Path, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from superboss.core.actors import Actor, get_actor
from superboss.core.errors import DomainError
from superboss.modules.audit.schemas import AuditEventInput
from superboss.modules.audit.service import AuditService
from superboss.modules.files.models import File
from superboss.modules.files.schemas import UploadComplete, UploadStart
from superboss.modules.files.service import FileService
from superboss.modules.files.storage import CompletedPart

router = APIRouter(prefix="/files", tags=["files"])


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
    request: Request, actor: Actor, file: File | None, file_id: UUID, outcome: str
) -> None:
    try:
        await AuditService(request.app.state.session_factory).record(
            AuditEventInput(
                actor=actor,
                action="file.download",
                object_type="file",
                object_id=file.id if file is not None else file_id,
                project_id=file.project_id if file is not None else None,
                outcome=outcome,
                request_id=UUID(request.state.request_id),
                metadata={"state": file.state.value} if file is not None else {},
            )
        )
    except Exception:  # noqa: BLE001 -- audit unavailability must not alter download authorization
        return


@router.post("/uploads", status_code=status.HTTP_201_CREATED)
async def start(
    command: UploadStart,
    idempotency_key: str = Header(alias="Idempotency-Key", pattern=r"^[!-~]{1,255}$"),
    actor: Actor = Depends(get_actor),
    service: FileService = Depends(get_service),
) -> dict[str, str]:
    upload = await service.start_upload(actor, command, idempotency_key)
    return {"upload_id": str(upload.id), "file_id": str(upload.file_id)}


@router.post("/uploads/{upload_id}/complete")
async def complete(
    request: Request,
    upload_id: UUID,
    command: UploadComplete,
    actor: Actor = Depends(get_actor),
    service: FileService = Depends(get_service),
) -> dict[str, str]:
    file = await service.complete_upload(
        actor, upload_id, [CompletedPart(p.part_number, p.etag) for p in command.parts]
    )
    await service.session.commit()
    dispatched = request.app.state.enqueue_file_scan(file.id)
    if isawaitable(dispatched):
        await dispatched
    await AuditService(request.app.state.session_factory).record(
        AuditEventInput(
            actor=actor,
            action="file.upload.complete",
            object_type="file",
            object_id=file.id,
            project_id=file.project_id,
            outcome="SUCCESS",
            request_id=UUID(request.state.request_id),
            metadata={"state": file.state.value, "size_bytes": file.size_bytes},
        )
    )
    return {"file_id": str(file.id), "state": file.state}


@router.post("/uploads/{upload_id}/parts/{part_number}")
async def part(
    upload_id: UUID,
    part_number: int = Path(ge=1, le=10000),
    actor: Actor = Depends(get_actor),
    service: FileService = Depends(get_service),
) -> dict[str, str]:
    return {"url": await service.presign_part(actor, upload_id, part_number)}


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
        await _record_download_audit(request, actor, file, file_id, "DENIED")
        raise
    file = await service.session.get(File, file_id)
    assert file is not None
    await _record_download_audit(request, actor, file, file_id, "SUCCESS")
    return {"url": url}
