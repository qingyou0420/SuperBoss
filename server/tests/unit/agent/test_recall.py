"""Memory recall hits keywords instead of the whole user sentence."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from superboss.core.actors import Actor
from superboss.modules.agent.models import AgentMemory, MemoryKind, MemoryStatus
from superboss.modules.agent.service import AgentService
from superboss.modules.users.models import Role, User


@pytest.mark.asyncio
async def test_recall_hits_project_name_inside_a_longer_question(
    db_session: AsyncSession, active_owner: User
) -> None:
    db_session.add(
        AgentMemory(
            kind=MemoryKind.FACT,
            content="星野合作是合作类项目",
            importance=4,
            status=MemoryStatus.ACTIVE,
        )
    )
    await db_session.flush()
    service = AgentService(db_session, Actor(active_owner.id, Role.OWNER))
    recalled = await service.recall("昨天那个合作项目现在什么状态")
    assert any("星野合作" in item["content"] for item in recalled)


@pytest.mark.asyncio
async def test_recall_keeps_keywords_after_polite_prefix(
    db_session: AsyncSession, active_owner: User
) -> None:
    db_session.add(
        AgentMemory(
            kind=MemoryKind.FACT,
            content="星野合作是合作类项目",
            importance=4,
            status=MemoryStatus.ACTIVE,
        )
    )
    db_session.add(
        AgentMemory(
            kind=MemoryKind.FACT,
            content="昨天开了周会",
            importance=3,
            status=MemoryStatus.ACTIVE,
        )
    )
    await db_session.flush()
    service = AgentService(db_session, Actor(active_owner.id, Role.OWNER))
    hit = await service.recall("帮我看看昨天那个合作项目")
    assert any("星野合作" in item["content"] for item in hit)
    noise = await service.recall("昨天那个")
    assert not any("昨天开了周会" in item["content"] for item in noise)


@pytest.mark.asyncio
async def test_recall_hits_odd_offset_keywords(
    db_session: AsyncSession, active_owner: User
) -> None:
    db_session.add(
        AgentMemory(
            kind=MemoryKind.FACT,
            content="星野合作是合作类项目",
            importance=4,
            status=MemoryStatus.ACTIVE,
        )
    )
    db_session.add(
        AgentMemory(
            kind=MemoryKind.PREFERENCE,
            content="老板偏好项目按季度复盘",
            importance=3,
            status=MemoryStatus.ACTIVE,
        )
    )
    await db_session.flush()
    service = AgentService(db_session, Actor(active_owner.id, Role.OWNER))
    milestone = await service.recall("给星野加个 10 月 20 日交付节点")
    assert any("星野合作" in item["content"] for item in milestone)
    glance = await service.recall("看一下星野项目")
    assert any("星野合作" in item["content"] for item in glance)
    xingye = next(i for i, item in enumerate(glance) if "星野合作" in item["content"])
    later = [i for i, item in enumerate(glance) if "按季度复盘" in item["content"]]
    assert not later or xingye < later[0]
