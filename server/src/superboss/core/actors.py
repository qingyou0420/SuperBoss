"""Actor resolution and project authorization policies."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from superboss.core.errors import ForbiddenError, UnauthenticatedError
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
    """Resolve every browser credential against live user and session state."""
    authorization = request.headers.get("Authorization", "")
    token = request.cookies.get("access_token")
    if token is None and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ")
    if not token:
        raise UnauthenticatedError()
    async for session in _session(request):
        service = AuthService(
            session,
            AuthRepository(session),
            UserRepository(session),
            None,
            request.app.state.settings,
        )
        try:
            user = await service.authenticate_access_token(token)
        except InvalidSession as error:
            raise UnauthenticatedError() from error
        project_ids: frozenset[UUID] = frozenset()
        if user.role == Role.STAFF:
            project_ids = frozenset(
                (await session.scalars(select(ProjectMember.project_id).where(ProjectMember.user_id == user.id))).all()
            )
        return Actor("user", user.id, user.role, project_ids, frozenset())
    raise UnauthenticatedError()


def require_owner(actor: Actor) -> None:
    if actor.kind != "user" or actor.role != Role.OWNER:
        raise ForbiddenError("PROJECT_CREATE_FORBIDDEN", "You cannot create projects")


def require_project_access(actor: Actor, project_id: UUID) -> None:
    if actor.kind == "user" and (actor.role == Role.OWNER or project_id in actor.project_ids):
        return
    raise ForbiddenError()
