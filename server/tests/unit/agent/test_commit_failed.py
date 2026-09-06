"""Failed card confirms persist FAILED instead of rolling back to PROPOSED."""

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from superboss.core.actors import Actor
from superboss.modules.agent.cards import commit_card
from superboss.modules.agent.models import (
    AgentCard,
    AgentConversation,
    CardKind,
    CardStatus,
)
from superboss.modules.users.models import Role, User


@pytest.mark.asyncio
async def test_commit_failure_keeps_failed_status(
    db_session: AsyncSession, active_owner: User
) -> None:
    conversation = AgentConversation(owner_id=active_owner.id, title="确认失败")
    db_session.add(conversation)
    await db_session.flush()
    card = AgentCard(
        conversation_id=conversation.id,
        kind=CardKind.FINANCE_ENTRY,
        payload={
            "kind": "COST",
            "scope": "PROJECT",
            "project_id": str(uuid4()),
            "amount_cents": 100,
            "occurred_on": "2026-09-01",
            "category": "外包",
        },
        status=CardStatus.PROPOSED,
    )
    db_session.add(card)
    await db_session.flush()
    result = await commit_card(
        db_session,
        Actor(active_owner.id, Role.OWNER),
        card,
        request_id=uuid4(),
        storage=None,
        audit=None,
    )
    await db_session.flush()
    saved = await db_session.get(AgentCard, card.id)
    assert result.status is CardStatus.FAILED
    assert saved is not None and saved.status is CardStatus.FAILED
    assert saved.error == "FINANCE_PROJECT_NOT_FOUND"
