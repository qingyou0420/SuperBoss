"""OWNER device management and non-browser device token endpoints."""

from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response, status

from superboss.core.actors import Actor, get_actor
from superboss.core.errors import DomainError, OwnerRequiredError, UnauthenticatedError
from superboss.modules.audit.schemas import AuditEventInput
from superboss.modules.audit.service import AuditService
from superboss.modules.devices.schemas import (
    DeviceMeRead,
    DevicePair,
    DeviceProjectRead,
    DeviceRefresh,
    DeviceTokenRead,
    OwnerDeviceRead,
    PairingCodeCreate,
    PairingCodeRead,
)
from superboss.modules.devices.service import (
    DeviceService,
    InvalidDeviceCredential,
    InvalidDeviceGrant,
)
from superboss.modules.users.models import Role

router = APIRouter(tags=["devices"])


def get_service(request: Request) -> DeviceService:
    return DeviceService(request.app.state.session_factory, request.app.state.settings)


def _request_id(request: Request) -> UUID:
    return UUID(request.state.request_id)


async def _require_owner(
    request: Request,
    actor: Actor,
    *,
    action: str,
    object_type: str,
    object_id: UUID | None = None,
) -> None:
    if actor.kind == "user" and actor.role == Role.OWNER:
        return
    await AuditService(request.app.state.session_factory).record(
        AuditEventInput(
            actor=actor,
            action=action,
            object_type=object_type,
            object_id=object_id,
            outcome="DENIED",
            request_id=_request_id(request),
            metadata={"reason": "OWNER_REQUIRED"},
        )
    )
    raise OwnerRequiredError()


def _validation_error() -> DomainError:
    return DomainError("VALIDATION_ERROR", "Request validation failed", 422)


@router.post(
    "/owner/devices/pairing-codes",
    response_model=PairingCodeRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_pairing_code(
    request: Request,
    command: PairingCodeCreate,
    actor: Actor = Depends(get_actor),
    service: DeviceService = Depends(get_service),
) -> PairingCodeRead:
    await _require_owner(
        request,
        actor,
        action="device.pairing_code.create",
        object_type="device_pairing_code",
    )
    try:
        issue = await service.create_pairing_code(
            actor.subject_id, command.project_ids, request_id=_request_id(request)
        )
    except InvalidDeviceGrant as error:
        raise _validation_error() from error
    return PairingCodeRead(raw_code=issue.raw_code, expires_at=issue.expires_at)


@router.get("/owner/devices", response_model=list[OwnerDeviceRead])
async def list_devices(
    request: Request,
    actor: Actor = Depends(get_actor),
    service: DeviceService = Depends(get_service),
) -> list[OwnerDeviceRead]:
    await _require_owner(
        request, actor, action="device.list", object_type="device"
    )
    devices = await service.list_devices(actor.subject_id)
    return [
        OwnerDeviceRead(
            id=device.id,
            name=device.name,
            paired_at=device.paired_at,
            last_used_at=device.last_used_at,
            revoked_at=device.revoked_at,
            status="REVOKED" if device.revoked_at is not None else "ACTIVE",
            projects=tuple(
                DeviceProjectRead.model_validate(project) for project in device.projects
            ),
        )
        for device in devices
    ]


@router.delete("/owner/devices/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_device(
    request: Request,
    device_id: UUID,
    actor: Actor = Depends(get_actor),
    service: DeviceService = Depends(get_service),
) -> Response:
    await _require_owner(
        request,
        actor,
        action="device.revoke",
        object_type="device",
        object_id=device_id,
    )
    try:
        await service.revoke(
            actor.subject_id, device_id, request_id=_request_id(request)
        )
    except InvalidDeviceGrant as error:
        raise _validation_error() from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/device-auth/pair", response_model=DeviceTokenRead)
async def pair_device(
    request: Request,
    command: DevicePair,
    service: DeviceService = Depends(get_service),
) -> DeviceTokenRead:
    try:
        pair = await service.pair(
            command.pairing_code,
            command.device_name,
            request_id=_request_id(request),
        )
    except InvalidDeviceCredential as error:
        raise UnauthenticatedError() from error
    except InvalidDeviceGrant as error:
        raise _validation_error() from error
    return DeviceTokenRead.model_validate(pair)


@router.post("/device-auth/refresh", response_model=DeviceTokenRead)
async def refresh_device(
    request: Request,
    command: DeviceRefresh,
    service: DeviceService = Depends(get_service),
) -> DeviceTokenRead:
    try:
        pair = await service.refresh(command.refresh_token, request_id=_request_id(request))
    except InvalidDeviceCredential as error:
        raise UnauthenticatedError() from error
    return DeviceTokenRead.model_validate(pair)


@router.get("/device-auth/me", response_model=DeviceMeRead)
async def device_me(
    request: Request,
    actor: Actor = Depends(get_actor),
    service: DeviceService = Depends(get_service),
) -> DeviceMeRead:
    authorization = request.headers.get("Authorization", "")
    if (
        actor.kind != "device"
        or not authorization.startswith("Bearer ")
        or request.cookies.get("access_token") is not None
        or request.cookies.get("refresh_token") is not None
    ):
        await AuditService(request.app.state.session_factory).record(
            AuditEventInput(
                actor=actor,
                action="device.me",
                object_type="device",
                outcome="DENIED",
                request_id=_request_id(request),
                metadata={"reason": "DEVICE_CREDENTIAL_REQUIRED"},
            )
        )
        raise UnauthenticatedError()
    try:
        device = await service.get_device(actor.subject_id)
    except InvalidDeviceCredential as error:
        raise UnauthenticatedError() from error
    return DeviceMeRead.model_validate(device)
