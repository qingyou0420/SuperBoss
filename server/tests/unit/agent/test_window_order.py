"""Window replay keeps tool rows after the matching assistant tool_calls."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from superboss.core.actors import Actor
from superboss.modules.agent.models import (
    AgentConversation,
    AgentMessage,
    MessageRole,
)
from superboss.modules.agent.service import AgentService
from superboss.modules.users.models import Role, User


@pytest.mark.asyncio
async def test_window_keeps_tool_after_assistant_and_starts_with_user(
    db_session: AsyncSession, active_owner: User
) -> None:
    conversation = AgentConversation(owner_id=active_owner.id, title="窗口")
    db_session.add(conversation)
    await db_session.flush()
    user = AgentMessage(
        conversation_id=conversation.id,
        role=MessageRole.USER,
        content="列一下项目",
    )
    assistant = AgentMessage(
        conversation_id=conversation.id,
        role=MessageRole.ASSISTANT,
        content="",
        tool_calls={
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "list_projects", "arguments": "{}"},
                }
            ]
        },
    )
    tool = AgentMessage(
        conversation_id=conversation.id,
        role=MessageRole.TOOL,
        content="[]",
        tool_calls={"tool_call_id": "call-1"},
    )
    reply = AgentMessage(
        conversation_id=conversation.id,
        role=MessageRole.ASSISTANT,
        content="目前没有项目。",
    )
    db_session.add_all([user, assistant, tool, reply])
    await db_session.flush()
    service = AgentService(db_session, Actor(active_owner.id, Role.OWNER))
    window = await service._window(conversation.id)
    assert window[0]["role"] == "user"
    tool_index = next(index for index, item in enumerate(window) if item["role"] == "tool")
    previous = window[tool_index - 1]
    assert previous["role"] == "assistant"
    assert previous["tool_calls"][0]["id"] == "call-1"
    assert window[tool_index]["tool_call_id"] == "call-1"
    assert not any(item["role"] == "system" for item in window)
