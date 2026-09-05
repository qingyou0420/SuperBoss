"""Finance application service."""

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import set_committed_value

from superboss.core.actors import Actor, require_owner, require_project_actor
from superboss.core.errors import NotFoundError
from superboss.core.security import utcnow
from superboss.modules.audit.schemas import AuditEventInput
from superboss.modules.audit.service import AuditService
from superboss.modules.finance.models import (
    CreatedVia,
    FinanceAdjustment,
    FinanceEntry,
    FinanceKind,
    FinanceScope,
    FinanceVisibility,
)
from superboss.modules.finance.schemas import (
    CompanyTotals,
    FinanceAdjustmentCreate,
    FinanceAdjustmentRead,
    FinanceEntryCreate,
    FinanceEntryRead,
    FinanceSummary,
    ProjectTotals,
)
from superboss.modules.projects.models import Project
from superboss.modules.users.models import Role


@dataclass(frozen=True)
class EffectiveEntry:
    id: UUID
    kind: FinanceKind
    scope: FinanceScope
    project_id: UUID | None
    amount_cents: int
    currency: str
    occurred_on: date
    category: str
    memo: str
    visibility: FinanceVisibility
    created_via: CreatedVia
    created_at: datetime
    adjustments: list[FinanceAdjustment]


def default_visibility(kind: FinanceKind, scope: FinanceScope) -> FinanceVisibility:
    if kind is FinanceKind.INCOME or scope is FinanceScope.COMPANY:
        return FinanceVisibility.MANAGEMENT
    return FinanceVisibility.ALL


def entry_is_visible(
    actor: Actor,
    kind: FinanceKind,
    scope: FinanceScope,
    visibility: FinanceVisibility,
) -> bool:
    if actor.role == Role.OWNER:
        return True
    if actor.role == Role.MANAGER:
        return visibility in {FinanceVisibility.ALL, FinanceVisibility.MANAGEMENT}
    if actor.role == Role.STAFF:
        return (
            kind is FinanceKind.COST
            and scope is FinanceScope.PROJECT
            and visibility is FinanceVisibility.ALL
        )
    return False


def apply_adjustments(entry: FinanceEntry) -> EffectiveEntry:
    amount_cents = entry.amount_cents
    occurred_on = entry.occurred_on
    category = entry.category
    memo = entry.memo
    visibility = entry.visibility
    for adjustment in entry.adjustments:
        if adjustment.field == "amount_cents":
            amount_cents = int(adjustment.new_value)
        elif adjustment.field == "occurred_on":
            occurred_on = date.fromisoformat(adjustment.new_value)
        elif adjustment.field == "category":
            category = adjustment.new_value
        elif adjustment.field == "memo":
            memo = adjustment.new_value
        elif adjustment.field == "visibility":
            visibility = FinanceVisibility(adjustment.new_value)
    return EffectiveEntry(
        id=entry.id,
        kind=entry.kind,
        scope=entry.scope,
        project_id=entry.project_id,
        amount_cents=amount_cents,
        currency=entry.currency,
        occurred_on=occurred_on,
        category=category,
        memo=memo,
        visibility=visibility,
        created_via=entry.created_via,
        created_at=entry.created_at,
        adjustments=list(entry.adjustments),
    )


def current_month() -> str:
    today = utcnow().date()
    return f"{today.year:04d}-{today.month:02d}"


def month_contains(value: date, month: str) -> bool:
    year, month_number = (int(part) for part in month.split("-"))
    return value.year == year and value.month == month_number


def _field_value(entry: EffectiveEntry, field: str) -> str:
    if field == "amount_cents":
        return str(entry.amount_cents)
    if field == "occurred_on":
        return entry.occurred_on.isoformat()
    if field == "visibility":
        return entry.visibility.value
    if field == "category":
        return entry.category
    return entry.memo


class FinanceService:
    def __init__(self, session: AsyncSession, audit_service: AuditService | None = None) -> None:
        self.session = session
        self.audit_service = audit_service

    async def _record(
        self,
        actor: Actor,
        action: str,
        request_id: UUID | None,
        entry: FinanceEntry,
        metadata: dict[str, object],
    ) -> None:
        if self.audit_service is None or request_id is None:
            return
        await self.audit_service.record(
            AuditEventInput(
                actor=actor,
                action=action,
                object_type="finance_entry",
                object_id=entry.id,
                project_id=entry.project_id,
                outcome="SUCCESS",
                request_id=request_id,
                metadata=metadata,
            )
        )

    async def _projects(self, ids: set[UUID]) -> dict[UUID, str]:
        if not ids:
            return {}
        rows = (await self.session.scalars(select(Project).where(Project.id.in_(ids)))).all()
        return {row.id: row.name for row in rows}

    def _to_read(self, entry: EffectiveEntry, names: dict[UUID, str]) -> FinanceEntryRead:
        return FinanceEntryRead(
            id=entry.id,
            kind=entry.kind,
            scope=entry.scope,
            project_id=entry.project_id,
            project_name=names.get(entry.project_id) if entry.project_id else None,
            amount_cents=entry.amount_cents,
            currency=entry.currency,
            occurred_on=entry.occurred_on,
            category=entry.category,
            memo=entry.memo,
            visibility=entry.visibility,
            created_via=entry.created_via,
            created_at=entry.created_at,
            adjustments=[FinanceAdjustmentRead.model_validate(item) for item in entry.adjustments],
        )

    async def _load(self) -> list[EffectiveEntry]:
        rows = list(
            (
                await self.session.scalars(
                    select(FinanceEntry)
                    .options(selectinload(FinanceEntry.adjustments))
                    .order_by(FinanceEntry.occurred_on.desc(), FinanceEntry.created_at.desc())
                    .limit(2000)
                )
            ).all()
        )
        return [apply_adjustments(row) for row in rows]

    def _visible(self, actor: Actor, entries: list[EffectiveEntry]) -> list[EffectiveEntry]:
        return [
            entry
            for entry in entries
            if entry_is_visible(actor, entry.kind, entry.scope, entry.visibility)
        ]

    async def list_entries(
        self, actor: Actor, *, month: str | None = None, project_id: UUID | None = None
    ) -> list[FinanceEntryRead]:
        require_project_actor(actor)
        selected = month or current_month()
        visible = self._visible(actor, await self._load())
        if selected:
            visible = [entry for entry in visible if month_contains(entry.occurred_on, selected)]
        if project_id is not None:
            visible = [entry for entry in visible if entry.project_id == project_id]
        names = await self._projects({entry.project_id for entry in visible if entry.project_id})
        return [self._to_read(entry, names) for entry in visible]

    async def summary(self, actor: Actor, month: str | None = None) -> FinanceSummary:
        require_project_actor(actor)
        selected = month or current_month()
        visible = [
            entry
            for entry in self._visible(actor, await self._load())
            if month_contains(entry.occurred_on, selected)
        ]
        include_income = actor.role != Role.STAFF
        include_company = actor.role != Role.STAFF
        company_cost = 0
        company_income = 0
        project_cost: dict[UUID, int] = {}
        project_income: dict[UUID, int] = {}
        for entry in visible:
            if entry.scope is FinanceScope.COMPANY:
                if not include_company:
                    continue
                if entry.kind is FinanceKind.COST:
                    company_cost += entry.amount_cents
                else:
                    company_income += entry.amount_cents
                continue
            if entry.project_id is None:
                continue
            if entry.kind is FinanceKind.COST:
                project_cost[entry.project_id] = (
                    project_cost.get(entry.project_id, 0) + entry.amount_cents
                )
            elif include_income:
                project_income[entry.project_id] = (
                    project_income.get(entry.project_id, 0) + entry.amount_cents
                )
        names = await self._projects(set(project_cost) | set(project_income))
        projects = []
        for project_id in sorted(
            set(project_cost) | set(project_income), key=lambda item: names.get(item, "")
        ):
            totals = ProjectTotals(
                project_id=project_id,
                project_name=names.get(project_id, "项目"),
                cost_cents=project_cost.get(project_id, 0),
                income_cents=project_income.get(project_id, 0) if include_income else None,
            )
            projects.append(totals)
        return FinanceSummary(
            month=selected,
            company=(
                CompanyTotals(cost_cents=company_cost, income_cents=company_income)
                if include_company
                else None
            ),
            projects=projects,
        )

    async def cost_alerts(self, actor: Actor) -> list[dict[str, str]]:
        require_project_actor(actor)
        current = await self.summary(actor)
        year, month_number = (int(part) for part in current.month.split("-"))
        previous = f"{year - 1}-12" if month_number == 1 else f"{year:04d}-{month_number - 1:02d}"
        prior = await self.summary(actor, previous)
        prior_costs = {item.project_id: item.cost_cents for item in prior.projects}
        alerts: list[dict[str, str]] = []
        for item in current.projects:
            old = prior_costs.get(item.project_id, 0)
            if old > 0 and item.cost_cents > old * 1.5:
                alerts.append(
                    {
                        "project_id": str(item.project_id),
                        "message": (f"项目《{item.project_name}》本月成本较上月上升超过 50%"),
                    }
                )
        return alerts

    async def create_entry(
        self,
        actor: Actor,
        command: FinanceEntryCreate,
        request_id: UUID | None = None,
        *,
        created_via: CreatedVia = CreatedVia.FORM,
        card_id: UUID | None = None,
    ) -> FinanceEntryRead:
        require_owner(actor)
        if command.scope is FinanceScope.PROJECT:
            project = await self.session.get(Project, command.project_id)
            if project is None:
                raise NotFoundError("FINANCE_PROJECT_NOT_FOUND", "Project not found")
        visibility = command.visibility or default_visibility(command.kind, command.scope)
        entry = FinanceEntry(
            kind=command.kind,
            scope=command.scope,
            project_id=command.project_id,
            amount_cents=command.amount_cents,
            occurred_on=command.occurred_on,
            category=command.category,
            memo=command.memo,
            visibility=visibility,
            created_by=actor.subject_id,
            created_via=created_via,
            card_id=card_id,
        )
        self.session.add(entry)
        await self.session.flush()
        set_committed_value(entry, "adjustments", [])
        await self._record(
            actor,
            "finance.entry.create",
            request_id,
            entry,
            {
                "kind": entry.kind.value,
                "scope": entry.scope.value,
                "amount_cents": entry.amount_cents,
                "visibility": entry.visibility.value,
                "created_via": created_via.value,
            },
        )
        names = await self._projects({entry.project_id} if entry.project_id else set())
        return self._to_read(apply_adjustments(entry), names)

    async def adjust_entry(
        self,
        actor: Actor,
        entry_id: UUID,
        command: FinanceAdjustmentCreate,
        request_id: UUID | None = None,
    ) -> FinanceEntryRead:
        require_owner(actor)
        entry = await self.session.scalar(
            select(FinanceEntry)
            .where(FinanceEntry.id == entry_id)
            .options(selectinload(FinanceEntry.adjustments))
        )
        if entry is None:
            raise NotFoundError("FINANCE_ENTRY_NOT_FOUND", "Finance entry not found")
        effective = apply_adjustments(entry)
        new_value = command.new_value
        old_value = _field_value(effective, command.field)
        if new_value == old_value:
            names = await self._projects({entry.project_id} if entry.project_id else set())
            return self._to_read(effective, names)
        adjustment = FinanceAdjustment(
            entry_id=entry.id,
            field=command.field,
            old_value=old_value,
            new_value=new_value,
            reason=command.reason,
            created_by=actor.subject_id,
        )
        self.session.add(adjustment)
        await self.session.flush()
        entry.adjustments.append(adjustment)
        await self._record(
            actor,
            "finance.entry.adjust",
            request_id,
            entry,
            {"field": command.field, "reason": command.reason},
        )
        names = await self._projects({entry.project_id} if entry.project_id else set())
        return self._to_read(apply_adjustments(entry), names)
