"""Least-privilege device intake and browser OWNER import views."""

from uuid import UUID

from fastapi import APIRouter, Depends, Header, Path, Query, Request, status

from superboss.core.actors import Actor, get_actor
from superboss.core.errors import FileDeliveryPendingError
from superboss.modules.files.schemas import UploadComplete
from superboss.modules.files.service import FileLifecycleService
from superboss.modules.files.storage import CompletedPart
from superboss.modules.imports.schemas import (
    ImportAttachmentRead,
    ImportJobCreate,
    ImportJobRead,
    ImportJobSubmitRead,
    ImportPartUrlRead,
    OwnerImportJobRead,
)
from superboss.modules.imports.service import ImportService

router = APIRouter(tags=["imports"])


def get_service(request: Request) -> ImportService:
    return ImportService(
        request.app.state.session_factory,
        request.app.state.object_storage,
    )


def _request_id(request: Request) -> UUID:
    return UUID(request.state.request_id)


@router.post(
    "/device/import-jobs",
    response_model=ImportJobRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_import(
    request: Request,
    command: ImportJobCreate,
    idempotency_key: str = Header(alias="Idempotency-Key", pattern=r"^[!-~]{1,255}$"),
    actor: Actor = Depends(get_actor),
    service: ImportService = Depends(get_service),
) -> ImportJobRead:
    result = await service.create(
        actor,
        command,
        idempotency_key,
        request_id=_request_id(request),
    )
    return ImportJobRead.model_validate(await service.view_job(result.id))


@router.post(
    "/device/import-jobs/{job_id}/attachments/{attachment_id}/parts/{part_number}",
    response_model=ImportPartUrlRead,
)
async def presign_import_part(
    request: Request,
    job_id: UUID,
    attachment_id: UUID,
    part_number: int = Path(ge=1, le=10000),
    actor: Actor = Depends(get_actor),
    service: ImportService = Depends(get_service),
) -> ImportPartUrlRead:
    url = await service.presign_part(
        actor,
        job_id,
        attachment_id,
        part_number,
        request_id=_request_id(request),
    )
    return ImportPartUrlRead(url=url)


@router.post(
    "/device/import-jobs/{job_id}/attachments/{attachment_id}/complete",
    response_model=ImportAttachmentRead,
)
async def complete_import_attachment(
    request: Request,
    job_id: UUID,
    attachment_id: UUID,
    command: UploadComplete,
    actor: Actor = Depends(get_actor),
    service: ImportService = Depends(get_service),
) -> ImportAttachmentRead:
    await service.complete_attachment(
        actor,
        job_id,
        attachment_id,
        [CompletedPart(part.part_number, part.etag) for part in command.parts],
        request_id=_request_id(request),
    )
    attachment = await service.view_attachment(job_id, attachment_id)
    delivered = await FileLifecycleService(
        request.app.state.session_factory,
        request.app.state.object_storage,
        request.app.state.enqueue_file_scan,
    ).deliver_completion(attachment.upload_id)
    if not delivered:
        raise FileDeliveryPendingError()
    return ImportAttachmentRead.model_validate(
        await service.view_attachment(job_id, attachment_id)
    )


@router.post(
    "/device/import-jobs/{job_id}/submit",
    response_model=ImportJobSubmitRead,
)
async def submit_import(
    request: Request,
    job_id: UUID,
    actor: Actor = Depends(get_actor),
    service: ImportService = Depends(get_service),
) -> ImportJobSubmitRead:
    result = await service.submit(
        actor,
        job_id,
        request_id=_request_id(request),
    )
    return ImportJobSubmitRead.model_validate(result)


@router.get(
    "/device/import-jobs/{job_id}",
    response_model=ImportJobRead,
)
async def read_import(
    request: Request,
    job_id: UUID,
    actor: Actor = Depends(get_actor),
    service: ImportService = Depends(get_service),
) -> ImportJobRead:
    return ImportJobRead.model_validate(
        await service.read(actor, job_id, request_id=_request_id(request))
    )


@router.get(
    "/owner/import-jobs",
    response_model=list[OwnerImportJobRead],
)
async def list_owner_imports(
    request: Request,
    limit: int = Query(default=100, ge=1, le=100),
    actor: Actor = Depends(get_actor),
    service: ImportService = Depends(get_service),
) -> list[OwnerImportJobRead]:
    views = await service.list_owner(
        actor,
        limit=limit,
        request_id=_request_id(request),
    )
    return [OwnerImportJobRead.model_validate(view) for view in views]
