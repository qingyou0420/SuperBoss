"""OWNER-only audit read API."""

from fastapi import APIRouter, Depends, Query, Request

from superboss.core.actors import Actor, require_role
from superboss.modules.audit.schemas import AuditRead
from superboss.modules.audit.service import AuditService
from superboss.modules.users.models import Role

router = APIRouter(prefix="/audit", tags=["audit"])
_owner = require_role(Role.OWNER)


@router.get("", response_model=list[AuditRead])
async def list_audit_events(
    request: Request,
    actor: Actor = Depends(_owner),
    limit: int = Query(default=50, ge=1, le=200),
    action: str | None = Query(default=None, max_length=255),
) -> list[AuditRead]:
    del actor
    events = await AuditService(request.app.state.session_factory).list_events(
        limit=limit, action=action
    )
    return [AuditRead.model_validate(item) for item in events]
