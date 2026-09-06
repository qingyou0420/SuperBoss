"""Failed card confirms persist FAILED instead of rolling back to PROPOSED."""

from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from superboss.core.actors import Actor
from superboss.core.errors import NotFoundError
from superboss.modules.agent.cards import commit_card
from superboss.modules.agent.models import (
    AgentCard,
    AgentConversation,
    CardKind,
    CardStatus,
)
from superboss.modules.agent.schemas import CardPatch
from superboss.modules.agent.service import AgentService
from superboss.modules.projects.models import Project
from superboss.modules.projects.service import ProjectService
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


@pytest.mark.asyncio
async def test_failed_project_create_does_not_leave_a_project(
    db_session: AsyncSession, active_owner: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fail_milestones(*_args: object, **_kwargs: object) -> None:
        raise NotFoundError("MILESTONE_NOT_FOUND", "boom")

    monkeypatch.setattr(ProjectService, "replace_milestones", fail_milestones)
    conversation = AgentConversation(owner_id=active_owner.id, title="项目失败")
    db_session.add(conversation)
    await db_session.flush()
    card = AgentCard(
        conversation_id=conversation.id,
        kind=CardKind.PROJECT_CREATE,
        payload={
            "name": "ShouldNotExist",
            "milestones": [{"title": "节点", "due_on": None, "done": False, "sort_order": 0}],
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
    leftover = await db_session.scalar(select(Project).where(Project.name == "ShouldNotExist"))
    assert result.status is CardStatus.FAILED
    assert leftover is None


@pytest.mark.asyncio
async def test_same_name_project_create_card_fails_closed(
    db_session: AsyncSession, active_owner: User
) -> None:
    db_session.add(Project(name="星野合作"))
    await db_session.flush()
    before = await db_session.scalar(select(func.count()).select_from(Project))
    conversation = AgentConversation(owner_id=active_owner.id, title="同名")
    db_session.add(conversation)
    await db_session.flush()
    card = AgentCard(
        conversation_id=conversation.id,
        kind=CardKind.PROJECT_CREATE,
        payload={"name": "星野合作"},
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
    after = await db_session.scalar(select(func.count()).select_from(Project))
    assert result.status is CardStatus.FAILED
    assert result.error == "PROJECT_NAME_CONFLICT"
    assert saved is not None and saved.status is CardStatus.FAILED
    assert after == before


@pytest.mark.asyncio
async def test_failed_card_can_be_patched_or_rejected(
    db_session: AsyncSession, active_owner: User
) -> None:
    conversation = AgentConversation(owner_id=active_owner.id, title="失败卡")
    db_session.add(conversation)
    await db_session.flush()
    card = AgentCard(
        conversation_id=conversation.id,
        kind=CardKind.PROJECT_CREATE,
        payload={"name": "星野合作"},
        status=CardStatus.FAILED,
        error="PROJECT_NAME_CONFLICT",
    )
    db_session.add(card)
    await db_session.flush()
    service = AgentService(db_session, Actor(active_owner.id, Role.OWNER))
    patched = await service.patch_card(card.id, CardPatch(payload={"name": "新项目"}))
    assert patched.status is CardStatus.PROPOSED
    assert patched.error is None
    card.status = CardStatus.FAILED
    await db_session.flush()
    rejected = await service.reject_card(card.id)
    assert rejected.status is CardStatus.REJECTED
