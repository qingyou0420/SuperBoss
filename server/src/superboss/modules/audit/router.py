"""OWNER-only audit read API."""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from superboss.core.actors import Actor, require_role
from superboss.core.db import get_session
from superboss.modules.audit.schemas import AuditRead
from superboss.modules.audit.service import AuditService
from superboss.modules.users.models import Role, User

router = APIRouter(prefix="/audit", tags=["audit"])
_owner = require_role(Role.OWNER)


@router.get("", response_model=list[AuditRead])
async def list_audit_events(
    request: Request,
    actor: Actor = Depends(_owner),
    session: AsyncSession = Depends(get_session),
    limit: int = Query(default=50, ge=1, le=200),
    action: str | None = Query(default=None, max_length=255),
) -> list[AuditRead]:
    del actor
    events = await AuditService(request.app.state.session_factory).list_events(
        limit=limit, action=action
    )
    actor_ids = {item.actor_id for item in events if item.actor_id}
    names: dict[UUID, str] = {}
    if actor_ids:
        users = (await session.scalars(select(User).where(User.id.in_(actor_ids)))).all()
        names = {user.id: user.display_name or user.username for user in users}
    return [
        AuditRead.model_validate(item).model_copy(
            update={"actor_name": names.get(item.actor_id)}
        )
        for item in events
    ]
