"""OWNER-only STAFF whitelist endpoints."""

from uuid import UUID

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from superboss.core.actors import Actor, get_actor
from superboss.core.db import get_session
from superboss.modules.audit.service import AuditService
from superboss.modules.users.schemas import (
    OwnerUserRead,
    PasswordResetRead,
    StaffCreate,
    StaffCreateRead,
    StaffUpdate,
)
from superboss.modules.users.service import OwnerUserService

router = APIRouter(prefix="/owner/users", tags=["owner-users"])


def get_service(request: Request, session: AsyncSession = Depends(get_session)) -> OwnerUserService:
    return OwnerUserService(session, AuditService(request.app.state.session_factory))


def request_id(request: Request) -> UUID:
    return UUID(request.state.request_id)


@router.get("", response_model=list[OwnerUserRead])
async def list_users(
    request: Request,
    actor: Actor = Depends(get_actor),
    service: OwnerUserService = Depends(get_service),
) -> list[OwnerUserRead]:
    current_request_id = request_id(request)
    users = await service.list_users(actor, current_request_id)
    return [OwnerUserRead.model_validate(user) for user in users]


@router.post("", response_model=StaffCreateRead, status_code=status.HTTP_201_CREATED)
async def create_staff(
    request: Request,
    command: StaffCreate,
    actor: Actor = Depends(get_actor),
    service: OwnerUserService = Depends(get_service),
) -> StaffCreateRead:
    current_request_id = request_id(request)
    created = await service.create_staff(actor, command, current_request_id)
    await service.commit_and_record_success(
        actor, "user.create", current_request_id, created.user.id
    )
    return StaffCreateRead.model_validate(created)


@router.patch("/{user_id}", response_model=OwnerUserRead)
async def update_staff(
    request: Request,
    user_id: UUID,
    command: StaffUpdate,
    actor: Actor = Depends(get_actor),
    service: OwnerUserService = Depends(get_service),
) -> OwnerUserRead:
    current_request_id = request_id(request)
    user = await service.update_staff(actor, user_id, command, current_request_id)
    await service.commit_and_record_success(actor, "user.update", current_request_id, user.id)
    return OwnerUserRead.model_validate(user)


@router.post("/{user_id}/password-reset", response_model=PasswordResetRead)
async def reset_staff_password(
    request: Request,
    user_id: UUID,
    actor: Actor = Depends(get_actor),
    service: OwnerUserService = Depends(get_service),
) -> PasswordResetRead:
    current_request_id = request_id(request)
    result = await service.reset_staff_password(actor, user_id, current_request_id)
    await service.commit_and_record_success(
        actor, "user.password.reset", current_request_id, user_id
    )
    return PasswordResetRead.model_validate(result)
