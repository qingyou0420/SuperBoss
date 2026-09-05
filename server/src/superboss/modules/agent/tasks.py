"""Celery delivery for memory extraction."""

import asyncio
from uuid import UUID

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from superboss.core.actors import Actor
from superboss.core.config import get_settings
from superboss.core.llm import llm_from_settings
from superboss.modules.agent.service import AgentService
from superboss.modules.users.models import Role
from superboss.workers.celery_app import celery_app

settings = get_settings()


async def execute_memory_extract(conversation_id: str) -> None:
    active = get_settings()
    engine = create_async_engine(active.database_url, pool_pre_ping=True)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            service = AgentService(
                session,
                Actor(UUID(int=0), Role.OWNER),
                llm=llm_from_settings(active),
            )
            await service.extract_memories(UUID(conversation_id))
            await session.commit()
    finally:
        await engine.dispose()


@celery_app.task(  # type: ignore[untyped-decorator]
    name="superboss.agent.extract_memories",
    acks_late=True,
    autoretry_for=(Exception,),
    max_retries=2,
    reject_on_worker_lost=True,
    retry_backoff=True,
    queue="file-scan",
)
def extract_memories_task(conversation_id: str) -> None:
    asyncio.run(execute_memory_extract(conversation_id))


def enqueue_memory_extract(conversation_id: UUID) -> None:
    extract_memories_task.apply_async(args=[str(conversation_id)], queue="file-scan")


async def execute_daily_digest() -> None:
    from datetime import timedelta

    from sqlalchemy import select

    from superboss.core.security import utcnow
    from superboss.modules.agent.models import (
        AgentMemory,
        AgentMessage,
        MemoryKind,
        MemoryStatus,
        MessageRole,
    )

    active = get_settings()
    engine = create_async_engine(active.database_url, pool_pre_ping=True)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            start = utcnow() - timedelta(days=1)
            rows = list(
                (
                    await session.scalars(
                        select(AgentMessage)
                        .where(
                            AgentMessage.created_at >= start,
                            AgentMessage.role.in_((MessageRole.USER, MessageRole.ASSISTANT)),
                        )
                        .order_by(AgentMessage.created_at)
                        .limit(50)
                    )
                ).all()
            )
            if not rows:
                return
            summary = "；".join(
                item.content.replace("\n", " ")[:80] for item in rows if item.content
            )[:1800]
            session.add(
                AgentMemory(
                    kind=MemoryKind.DAILY_DIGEST,
                    content=f"昨日纪要：{summary}",
                    importance=4,
                    status=MemoryStatus.ACTIVE,
                )
            )
            await session.commit()
    finally:
        await engine.dispose()


@celery_app.task(  # type: ignore[untyped-decorator]
    name="superboss.agent.daily_digest",
    acks_late=True,
    queue="file-scan",
)
def daily_digest_task() -> None:
    asyncio.run(execute_daily_digest())
