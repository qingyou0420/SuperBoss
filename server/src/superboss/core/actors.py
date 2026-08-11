"""Actor resolution and project authorization policies."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from superboss.core.errors import (
    ForbiddenError,
    PasswordChangeRequiredError,
    UnauthenticatedError,
)
from superboss.modules.auth.repository import AuthRepository
from superboss.modules.auth.service import AuthService, InvalidSession
from superboss.modules.projects.models import ProjectMember
from superboss.modules.users.models import Role
from superboss.modules.users.repository import UserRepository


@dataclass(frozen=True)
class Actor:
    kind: Literal["user", "device", "system"]
    subject_id: UUID
    role: Role | None
    project_ids: frozenset[UUID]
    scopes: frozenset[str]


async def _session(request: Request) -> AsyncIterator[AsyncSession]:
    session = request.app.state.session_factory()
    try:
        yield session
    finally:
        await session.close()


async def get_actor(request: Request) -> Actor:
    """Resolve browser or exact device credentials against live server state."""
    cached = getattr(request.state, "resolved_actor", None)
    if isinstance(cached, Actor):
        return cached
    authorization = request.headers.get("Authorization", "")
    token = request.cookies.get("access_token")
    has_browser_cookie = token is not None or request.cookies.get("refresh_token") is not None
    if token is None and not has_browser_cookie and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ")
    if not token:
        raise UnauthenticatedError()
    async for session in _session(request):
        service = AuthService(
            session,
            AuthRepository(session),
            UserRepository(session),
            request.app.state.settings,
        )
        try:
            user = await service.authenticate_access_token(token)
        except InvalidSession as error:
            if has_browser_cookie:
                raise UnauthenticatedError() from error
            from superboss.modules.devices.service import (
                DeviceService,
                InvalidDeviceCredential,
            )
            try:
                actor = await DeviceService(
                    request.app.state.session_factory, request.app.state.settings
                ).authenticate_access_token(
                    token, request_id=UUID(request.state.request_id)
                )
            except InvalidDeviceCredential as device_error:
                raise UnauthenticatedError() from device_error
            request.state.resolved_actor = actor
            return actor
        else:
            if user.must_change_password:
                raise PasswordChangeRequiredError()
            project_ids: frozenset[UUID] = frozenset()
            if user.role == Role.STAFF:
                project_ids = frozenset(
                    (
                        await session.scalars(
                            select(ProjectMember.project_id).where(
                                ProjectMember.user_id == user.id
                            )
                        )
                    ).all()
                )
            actor = Actor("user", user.id, user.role, project_ids, frozenset())
            request.state.resolved_actor = actor
            return actor
    raise UnauthenticatedError()


def require_owner(actor: Actor) -> None:
    if actor.kind != "user" or actor.role != Role.OWNER:
        raise ForbiddenError("PROJECT_CREATE_FORBIDDEN", "You cannot create projects")


def require_project_access(actor: Actor, project_id: UUID) -> None:
    if actor.kind != "user":
        raise ForbiddenError()
    if actor.role == Role.OWNER:
        return
    if actor.role == Role.STAFF and project_id in actor.project_ids:
        return
    raise ForbiddenError()


def require_project_actor(actor: Actor) -> None:
    """Reject actor kinds and role shapes unsupported by project APIs."""
    if actor.kind != "user" or actor.role not in {Role.OWNER, Role.STAFF}:
        raise ForbiddenError()
