"""Knowledge routes."""

from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from superboss.core.actors import Actor, get_actor
from superboss.modules.knowledge.schemas import (
    KnowledgeDocCreate,
    KnowledgeDocRead,
    KnowledgeDocUpdate,
)
from superboss.modules.knowledge.service import KnowledgeService

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    session = request.app.state.session_factory()
    try:
        yield session
    except Exception:
        await session.rollback()
        raise
    else:
        await session.commit()
    finally:
        await session.close()


def get_service(session: AsyncSession = Depends(get_session)) -> KnowledgeService:
    return KnowledgeService(session)


@router.get("", response_model=list[KnowledgeDocRead])
async def list_docs(
    actor: Actor = Depends(get_actor),
    service: KnowledgeService = Depends(get_service),
    q: str | None = Query(default=None, max_length=80),
) -> list[KnowledgeDocRead]:
    return [KnowledgeDocRead.model_validate(item) for item in await service.list_docs(actor, q)]


@router.get("/{doc_id}", response_model=KnowledgeDocRead)
async def get_doc(
    doc_id: UUID,
    actor: Actor = Depends(get_actor),
    service: KnowledgeService = Depends(get_service),
) -> KnowledgeDocRead:
    return KnowledgeDocRead.model_validate(await service.get(actor, doc_id))


@router.post("", response_model=KnowledgeDocRead, status_code=status.HTTP_201_CREATED)
async def create_doc(
    command: KnowledgeDocCreate,
    actor: Actor = Depends(get_actor),
    service: KnowledgeService = Depends(get_service),
) -> KnowledgeDocRead:
    return KnowledgeDocRead.model_validate(await service.create(actor, command))


@router.patch("/{doc_id}", response_model=KnowledgeDocRead)
async def update_doc(
    doc_id: UUID,
    command: KnowledgeDocUpdate,
    actor: Actor = Depends(get_actor),
    service: KnowledgeService = Depends(get_service),
) -> KnowledgeDocRead:
    return KnowledgeDocRead.model_validate(await service.update(actor, doc_id, command))
