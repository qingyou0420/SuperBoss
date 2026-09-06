"""Memory extract task commits memories even with the system actor."""

from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from superboss.core.actors import Actor
from superboss.core.llm import LLMResult
from superboss.modules.agent.models import (
    AgentConversation,
    AgentMemory,
    AgentMessage,
    MemoryStatus,
    MessageRole,
)
from superboss.modules.agent.service import AgentService
from superboss.modules.users.models import Role, User


class _FakeLLM:
    available = True

    async def complete(self, messages: list[dict[str, object]], tools: object) -> LLMResult:
        del tools
        prompt = str(messages[0].get("content") or "")
        if "提取" in prompt:
            return LLMResult(
                content='[{"kind":"FACT","content":"星野合作是合作类项目","importance":4}]'
            )
        return LLMResult(content="老板在推进星野合作。")


@pytest.mark.asyncio
async def test_extract_memories_with_system_actor_writes_memory_and_summary(
    db_session: AsyncSession, active_owner: User
) -> None:
    conversation = AgentConversation(owner_id=active_owner.id, title="抽取")
    db_session.add(conversation)
    await db_session.flush()
    for index in range(18):
        db_session.add(
            AgentMessage(
                conversation_id=conversation.id,
                role=MessageRole.USER if index % 2 == 0 else MessageRole.ASSISTANT,
                content=f"闲聊{index} 星野合作",
            )
        )
    await db_session.flush()
    service = AgentService(
        db_session,
        Actor(UUID(int=0), Role.OWNER),
        llm=_FakeLLM(),
    )
    await service.extract_memories(conversation.id)
    await db_session.flush()
    count = await db_session.scalar(
        select(func.count()).select_from(AgentMemory).where(AgentMemory.status == MemoryStatus.ACTIVE)
    )
    saved = await db_session.get(AgentConversation, conversation.id)
    assert count and count > 0
    assert saved is not None and saved.summary
