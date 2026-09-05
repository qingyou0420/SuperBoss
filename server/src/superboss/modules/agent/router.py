"""OWNER-only 霜月 routes."""

import json
from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from superboss.core.actors import Actor, require_role
from superboss.modules.agent.schemas import (
    CardPatch,
    CardRead,
    CardRevise,
    ChatMessageCreate,
    ChatTurnRead,
    ConversationCreate,
    ConversationRead,
    MemoryPatch,
    MemoryRead,
    MessageRead,
    SoulPreview,
    SoulRead,
    SoulWrite,
)
from superboss.modules.agent.service import AgentService
from superboss.modules.audit.service import AuditService
from superboss.modules.users.models import Role

router = APIRouter(prefix="/agent", tags=["agent"])
_owner = require_role(Role.OWNER)


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


def get_service(
    request: Request,
    actor: Actor = Depends(_owner),
    session: AsyncSession = Depends(get_session),
) -> AgentService:
    return AgentService(
        session,
        actor,
        llm=request.app.state.llm_client,
        storage=request.app.state.object_storage,
        audit=AuditService(request.app.state.session_factory),
        enqueue_extract=request.app.state.enqueue_memory_extract,
    )


@router.get("/conversations", response_model=list[ConversationRead])
async def list_conversations(
    service: AgentService = Depends(get_service),
    q: str | None = Query(default=None, max_length=80),
) -> list[ConversationRead]:
    return await service.list_conversations(q)


@router.post("/conversations", response_model=ConversationRead, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    command: ConversationCreate = ConversationCreate(),
    service: AgentService = Depends(get_service),
) -> ConversationRead:
    return await service.create_conversation(command.title)


@router.post("/conversations/{conversation_id}/archive", status_code=status.HTTP_204_NO_CONTENT)
async def archive_conversation(
    conversation_id: UUID, service: AgentService = Depends(get_service)
) -> None:
    await service.archive_conversation(conversation_id)


@router.get("/conversations/{conversation_id}/messages", response_model=list[MessageRead])
async def list_messages(
    conversation_id: UUID, service: AgentService = Depends(get_service)
) -> list[MessageRead]:
    return await service.list_messages(conversation_id)


@router.get("/conversations/{conversation_id}/cards", response_model=list[CardRead])
async def list_cards(
    conversation_id: UUID, service: AgentService = Depends(get_service)
) -> list[CardRead]:
    return await service.list_cards(conversation_id)


@router.post("/conversations/{conversation_id}/messages", response_model=ChatTurnRead)
async def chat(
    conversation_id: UUID,
    command: ChatMessageCreate,
    service: AgentService = Depends(get_service),
) -> ChatTurnRead:
    return await service.chat(conversation_id, command)


@router.post("/conversations/{conversation_id}/messages/stream")
async def chat_stream(
    request: Request,
    conversation_id: UUID,
    command: ChatMessageCreate,
    actor: Actor = Depends(_owner),
) -> StreamingResponse:
    session = request.app.state.session_factory()
    service = AgentService(
        session,
        actor,
        llm=request.app.state.llm_client,
        storage=request.app.state.object_storage,
        audit=AuditService(request.app.state.session_factory),
        enqueue_extract=request.app.state.enqueue_memory_extract,
    )

    async def events():
        try:
            async for kind, payload in service.chat_stream(conversation_id, command):
                data = (
                    payload.model_dump(mode="json") if hasattr(payload, "model_dump") else payload
                )
                yield f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    return StreamingResponse(events(), media_type="text/event-stream")


@router.post("/cards/{card_id}/confirm", response_model=CardRead)
async def confirm_card(
    request: Request,
    card_id: UUID,
    service: AgentService = Depends(get_service),
) -> CardRead:
    return await service.confirm_card(card_id, UUID(request.state.request_id))


@router.patch("/cards/{card_id}", response_model=CardRead)
async def patch_card(
    card_id: UUID,
    command: CardPatch,
    service: AgentService = Depends(get_service),
) -> CardRead:
    return await service.patch_card(card_id, command)


@router.post("/cards/{card_id}/revise", response_model=ChatTurnRead)
async def revise_card(
    card_id: UUID,
    command: CardRevise,
    service: AgentService = Depends(get_service),
) -> ChatTurnRead:
    return await service.revise_card(card_id, command)


@router.post("/cards/{card_id}/reject", response_model=CardRead)
async def reject_card(card_id: UUID, service: AgentService = Depends(get_service)) -> CardRead:
    return await service.reject_card(card_id)


@router.get("/soul", response_model=list[SoulRead])
async def list_soul(service: AgentService = Depends(get_service)) -> list[SoulRead]:
    return await service.list_soul()


@router.post("/soul", response_model=SoulRead, status_code=status.HTTP_201_CREATED)
async def write_soul(
    request: Request,
    command: SoulWrite,
    service: AgentService = Depends(get_service),
) -> SoulRead:
    return await service.write_soul(command, UUID(request.state.request_id))


@router.post("/soul/{soul_id}/activate", response_model=SoulRead)
async def activate_soul(
    request: Request,
    soul_id: UUID,
    service: AgentService = Depends(get_service),
) -> SoulRead:
    return await service.activate_soul(soul_id, UUID(request.state.request_id))


@router.get("/soul/preview", response_model=SoulPreview)
async def preview_soul(service: AgentService = Depends(get_service)) -> SoulPreview:
    return await service.preview_soul()


@router.get("/memories", response_model=list[MemoryRead])
async def list_memories(service: AgentService = Depends(get_service)) -> list[MemoryRead]:
    return await service.list_memories()


@router.patch("/memories/{memory_id}", response_model=MemoryRead)
async def patch_memory(
    memory_id: UUID,
    command: MemoryPatch,
    service: AgentService = Depends(get_service),
) -> MemoryRead:
    return await service.patch_memory(memory_id, command)
