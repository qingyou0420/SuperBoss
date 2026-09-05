"""Actor resolution and project authorization policies."""

from collections.abc import AsyncIterator, Callable, Coroutine
from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from superboss.core.errors import (
    ForbiddenError,
    PasswordChangeRequiredError,
    UnauthenticatedError,
)
from superboss.modules.auth.service import AuthService, InvalidSession
from superboss.modules.projects.models import ProjectMember
from superboss.modules.users.models import Role

_SIGNED_IN = frozenset({Role.OWNER, Role.MANAGER, Role.STAFF})


@dataclass(frozen=True)
class Actor:
    subject_id: UUID
    role: Role | None
    project_ids: frozenset[UUID] = frozenset()


async def _session(request: Request) -> AsyncIterator[AsyncSession]:
    session = request.app.state.session_factory()
    try:
        yield session
    finally:
        await session.close()


async def get_actor(request: Request) -> Actor:
    """Resolve the browser access cookie against live server state."""
    cached = getattr(request.state, "resolved_actor", None)
    if isinstance(cached, Actor):
        return cached
    token = request.cookies.get("access_token")
    if not token:
        raise UnauthenticatedError()
    async for session in _session(request):
        service = AuthService(session, request.app.state.settings)
        try:
            user = await service.authenticate_access_token(token)
        except InvalidSession as error:
            raise UnauthenticatedError() from error
        if user.must_change_password:
            raise PasswordChangeRequiredError()
        project_ids: frozenset[UUID] = frozenset()
        if user.role != Role.OWNER:
            project_ids = frozenset(
                (
                    await session.scalars(
                        select(ProjectMember.project_id).where(
                            ProjectMember.user_id == user.id
                        )
                    )
                ).all()
            )
        actor = Actor(user.id, user.role, project_ids)
        request.state.resolved_actor = actor
        return actor
    raise UnauthenticatedError()


def require_role(
    *roles: Role,
) -> Callable[[Actor], Coroutine[object, object, Actor]]:
    allowed = frozenset(roles)

    async def _enforce(actor: Actor = Depends(get_actor)) -> Actor:
        if actor.role not in allowed:
            raise ForbiddenError("FORBIDDEN", "You cannot perform this action")
        return actor

    return _enforce


def require_owner(actor: Actor) -> None:
    if actor.role != Role.OWNER:
        raise ForbiddenError("FORBIDDEN", "You cannot perform this action")


def require_project_access(actor: Actor, project_id: UUID) -> None:
    if actor.role in {Role.OWNER, Role.MANAGER}:
        return
    if actor.role == Role.STAFF and project_id in actor.project_ids:
        return
    raise ForbiddenError()


def require_project_actor(actor: Actor) -> None:
    if actor.role not in _SIGNED_IN:
        raise ForbiddenError()
