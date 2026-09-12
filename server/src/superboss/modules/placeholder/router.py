"""Owner-triggered placeholder seed."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from superboss.core.actors import Actor, get_actor, require_owner
from superboss.core.db import get_session
from superboss.modules.placeholder.seed import placeholder_status, seed_placeholder

router = APIRouter(prefix="/placeholder", tags=["placeholder"])


@router.get("/status")
async def status(
    actor: Actor = Depends(get_actor),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    del actor
    return await placeholder_status(session)


@router.post("/seed")
async def seed(
    actor: Actor = Depends(get_actor),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    require_owner(actor)
    return await seed_placeholder(session, actor.subject_id)
