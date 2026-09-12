"""Idempotent placeholder business records for development."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import set_committed_value

from superboss.core.security import utcnow
from superboss.modules.directory.models import (
    CommunicationStatus,
    DirectoryCommunication,
    DirectoryEntry,
)
from superboss.modules.finance.models import (
    CompanyMonthCost,
    CompanySetting,
    CreatedVia,
    FinanceEntry,
    FinanceKind,
    FinanceScope,
    FinanceVisibility,
    RewardAllocation,
)
from superboss.modules.knowledge.models import KnowledgeDoc, KnowledgePoint, KnowledgeStatus
from superboss.modules.projects.models import (
    NodeStatus,
    Project,
    ProjectNode,
    ProjectScheduleChange,
    ProjectStage,
    ProjectStatus,
)
from superboss.modules.projects.workflow import plan_from_start
from superboss.modules.users.models import Role, User

PLACEHOLDER_SOURCE = "placeholder.xlsx"
PLACEHOLDER_PROJECT = "【占位】云栖里业主大会"
PLACEHOLDER_ACTIVE = "【占位】港城花园业主大会"
PLACEHOLDER_BATCH = "placeholder-v1"
PLACEHOLDER_ESTATE = "【占位】云栖里"


async def placeholder_status(session: AsyncSession) -> dict[str, Any]:
    project = await session.scalar(select(Project).where(Project.name == PLACEHOLDER_PROJECT))
    opening = await session.get(CompanySetting, "opening_balance")
    return {
        "seeded": project is not None,
        "is_placeholder": True if opening is None else bool(opening.is_placeholder),
        "project_name": PLACEHOLDER_PROJECT if project is not None else "",
        "note": "占位数据，真实资料到位后替换。地图不伪造经纬度。",
    }


async def _setting(session: AsyncSession, key: str, value: dict[str, object]) -> None:
    row = await session.get(CompanySetting, key)
    if row is None:
        session.add(CompanySetting(key=key, value_json=value, is_placeholder=True))
        return
    if row.is_placeholder:
        row.value_json = value


async def _estate(
    session: AsyncSession,
    *,
    name: str,
    row: int,
    street: str,
    community: str,
    extra: dict[str, object],
) -> DirectoryEntry:
    existing = await session.scalar(
        select(DirectoryEntry).where(
            DirectoryEntry.source_filename == PLACEHOLDER_SOURCE,
            DirectoryEntry.source_row == row,
        )
    )
    if existing is not None:
        return existing
    entry = DirectoryEntry(
        name=name,
        district="龙文区",
        street=street,
        community=community,
        property_company="占位物业",
        households=968,
        floor_area="占位-待核实",
        estate_type="占位",
        manager_name="占位联系人",
        phone="",
        extra=extra,
        source_filename=PLACEHOLDER_SOURCE,
        source_sheet="Sheet1",
        source_row=row,
    )
    session.add(entry)
    await session.flush()
    return entry


async def _talk(
    session: AsyncSession,
    entry: DirectoryEntry,
    owner_id: UUID,
    status: CommunicationStatus,
    demand: str,
    result: str,
) -> None:
    exists = await session.scalar(
        select(DirectoryCommunication.id)
        .where(DirectoryCommunication.entry_id == entry.id)
        .limit(1)
    )
    if exists is not None:
        return
    session.add(
        DirectoryCommunication(
            entry_id=entry.id,
            occurred_on=date(2026, 8, 12),
            contact_name="占位-社区干部",
            contact_role="社区",
            demand=demand,
            result=result,
            next_step="占位：下周带资料上门",
            status=status,
            created_by=owner_id,
        )
    )


async def _project(session: AsyncSession, name: str, start: date, fee: int) -> Project:
    existing = await session.scalar(
        select(Project).options(selectinload(Project.nodes)).where(Project.name == name)
    )
    if existing is not None:
        return existing
    project = Project(
        name=name,
        description="占位项目。真实小区、费用和流程到位后替换。",
        status=ProjectStatus.ACTIVE,
        stage=ProjectStage.ACTIVE,
        starts_on=start,
        service_fee_cents=fee,
    )
    session.add(project)
    await session.flush()
    set_committed_value(project, "nodes", [])
    set_committed_value(project, "milestones", [])
    set_committed_value(project, "schedule_changes", [])
    for index, item in enumerate(plan_from_start(start)):
        project.nodes.append(
            ProjectNode(
                sort_order=index,
                title=item["title"],
                planned_start=item["planned_start"],
                planned_end=item["planned_end"],
                preparation=item["preparation"],
                document_name=item["document_name"],
                photo_required=item["photo_required"],
            )
        )
    if project.nodes and project.nodes[-1].planned_end:
        project.due_on = project.nodes[-1].planned_end
    await session.flush()
    return project


async def _finish_demo(session: AsyncSession, project: Project, owner_id: UUID) -> None:
    if any(node.status is NodeStatus.DONE for node in project.nodes):
        return
    photo = next(item for item in project.nodes if item.photo_required)
    before = [
        {
            "title": node.title,
            "planned_end": node.planned_end.isoformat() if node.planned_end else None,
        }
        for node in project.nodes
    ]
    for node in project.nodes:
        if node.sort_order >= photo.sort_order and node.planned_start and node.planned_end:
            node.planned_start = node.planned_start + timedelta(days=5)
            node.planned_end = node.planned_end + timedelta(days=5)
    session.add(
        ProjectScheduleChange(
            project_id=project.id,
            node_id=photo.id,
            days=5,
            reason="占位：社区改期五天",
            before_json=before,
            after_json=[],
            created_by=owner_id,
        )
    )
    now = utcnow()
    for node in project.nodes:
        node.status = NodeStatus.DONE
        node.completed_at = now
        node.completed_by = owner_id
        node.evidence = ["占位-公示照片.jpg"] if node.photo_required else ["占位-定稿.pdf"]
    project.progress_percent = 100
    project.stage = ProjectStage.REVIEW
    if project.nodes and project.nodes[-1].planned_end:
        project.due_on = project.nodes[-1].planned_end


async def _knowledge(session: AsyncSession, project: Project, owner_id: UUID) -> None:
    exists = await session.scalar(
        select(KnowledgeDoc.id).where(KnowledgeDoc.title.like("【占位】%")).limit(1)
    )
    if exists is not None:
        return
    pack = [
        (
            "【占位】筹备与资料确认",
            "筹备与资料确认",
            "核对本小区委托书、议题和联系人。未提供的法规依据标为待补充。",
            "占位：老板尚未导入 Kimi 整理包",
            True,
        ),
        (
            "【占位】名单公示未通过稿",
            "候选人名单公示",
            "缺现场照片说明，未作为定稿。",
            "占位：社区要求补充公示位置照片",
            False,
        ),
        (
            "【占位】名单公示参考定稿",
            "候选人名单公示",
            "采用经确认的候选人名单和公示照片留存方式。",
            "占位：最终采用版本，仅作参考",
            True,
        ),
        (
            "【占位】投票与现场执行",
            "投票与现场执行",
            "按最新日程准备投票材料。员工工作文件不会自动成为知识。",
            "占位：现场物料清单待老板确认",
            True,
        ),
    ]
    for title, stage, body, reason, canonical in pack:
        doc = KnowledgeDoc(
            title=title,
            body_md=body,
            tags=["占位", stage],
            status=KnowledgeStatus.PUBLISHED,
            project_id=project.id,
            stage_title=stage,
            change_reason=reason,
            is_canonical=canonical,
            created_by=owner_id,
        )
        doc.points.append(KnowledgePoint(title=stage, body_md=body, sort_order=0))
        session.add(doc)


async def _finance(session: AsyncSession, project: Project, owner_id: UUID) -> None:
    exists = await session.scalar(
        select(FinanceEntry.id).where(FinanceEntry.batch_key == PLACEHOLDER_BATCH).limit(1)
    )
    if exists is not None:
        return
    rows = [
        (FinanceKind.COST, FinanceScope.PROJECT, project.id, 150_000, date(2026, 8, 25), "印刷"),
        (FinanceKind.COST, FinanceScope.PROJECT, project.id, 250_000, date(2026, 9, 2), "物料"),
        (
            FinanceKind.INCOME,
            FinanceScope.PROJECT,
            project.id,
            1_500_000,
            date(2026, 8, 31),
            "首款",
        ),
        (
            FinanceKind.INCOME,
            FinanceScope.PROJECT,
            project.id,
            1_500_000,
            date(2026, 9, 10),
            "尾款",
        ),
        (FinanceKind.COST, FinanceScope.COMPANY, None, 800_000, date(2026, 9, 5), "房租"),
    ]
    for kind, scope, project_id, amount, occurred, category in rows:
        session.add(
            FinanceEntry(
                kind=kind,
                scope=scope,
                project_id=project_id,
                amount_cents=amount,
                occurred_on=occurred,
                category=category,
                memo="占位账本，真实 Excel 与凭证到位后替换",
                visibility=(
                    FinanceVisibility.MANAGEMENT
                    if kind is FinanceKind.INCOME or scope is FinanceScope.COMPANY
                    else FinanceVisibility.ALL
                ),
                created_by=owner_id,
                created_via=CreatedVia.FORM,
                batch_key=PLACEHOLDER_BATCH,
                paid_on=occurred if kind is FinanceKind.INCOME else None,
                paid_cents=amount if kind is FinanceKind.INCOME else None,
            )
        )
    for month, cents in (("2026-08", 700_000), ("2026-09", 800_000)):
        if await session.get(CompanyMonthCost, month) is None:
            session.add(CompanyMonthCost(month=month, amount_cents=cents))
    allocations = [
        ("SURPLUS", "占位-主责", 120_000, "节余奖金人员分配待真实记录替换"),
        ("SURPLUS", "占位-协助", 80_000, "与抽成比例分开保存"),
        ("POOL", "占位-执行甲", 100_000, "执行部抽成内部比例占位"),
        ("POOL", "占位-执行乙", 100_000, "执行部抽成内部比例占位"),
        ("POOL", "占位-执行丙", 100_000, "执行部抽成内部比例占位"),
    ]
    allocation_kind: str
    for allocation_kind, person, cents, note in allocations:
        session.add(
            RewardAllocation(
                id=uuid4(),
                project_id=project.id,
                kind=allocation_kind,
                person_name=person,
                share_cents=cents,
                is_placeholder=True,
                note=note,
            )
        )


async def seed_placeholder(session: AsyncSession, owner_id: UUID) -> dict[str, Any]:
    await _setting(
        session,
        "opening_balance",
        {
            "cents": 50_000_000,
            "as_of": "2026-01-01",
            "account_scope": "占位：账户范围未提供",
        },
    )
    await _setting(session, "payroll_same_day", {"rule": "THIS_MONTH"})
    yunqi = await _estate(
        session,
        name=PLACEHOLDER_ESTATE,
        row=1,
        street="步文街道",
        community="天亭社区",
        extra={
            "_placeholder": True,
            "业委会": "占位-已成立待核实",
            "临委会": "占位-无",
            "定位": "占位-无经纬度，不画点",
        },
    )
    gangcheng = await _estate(
        session,
        name="【占位】港城花园",
        row=2,
        street="蓝田街道",
        community="占位社区",
        extra={
            "_placeholder": True,
            "业委会": "占位-未知",
            "临委会": "占位-待核实",
            "定位": "占位-无经纬度，不画点",
        },
    )
    await _talk(
        session,
        yunqi,
        owner_id,
        CommunicationStatus.COMMISSIONED,
        "成立业委会会务协助，服务费三万元（占位）",
        "社区确认委托，正式合同后补",
    )
    await _talk(
        session,
        gangcheng,
        owner_id,
        CommunicationStatus.CONTACTING,
        "日常议题表决，费用未定（占位）",
        "下周继续联系，不登记确定收入",
    )
    done = await _project(session, PLACEHOLDER_PROJECT, date(2026, 8, 20), 3_000_000)
    active = await _project(session, PLACEHOLDER_ACTIVE, date(2026, 9, 1), 3_000_000)
    await _finish_demo(session, done, owner_id)
    yunqi.project_id = done.id
    await _knowledge(session, done, owner_id)
    await _finance(session, done, owner_id)
    await session.flush()
    return {
        "seeded": True,
        "is_placeholder": True,
        "completed_project_id": str(done.id),
        "active_project_id": str(active.id),
        "note": "占位数据，真实资料到位后替换。",
    }


async def seed_for_owner(session: AsyncSession) -> dict[str, Any]:
    owner = await session.scalar(select(User).where(User.role == Role.OWNER))
    if owner is None:
        return {"seeded": False, "error": "OWNER_MISSING"}
    result = await seed_placeholder(session, owner.id)
    await session.commit()
    return result
