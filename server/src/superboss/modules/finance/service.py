"""Finance application service."""

import re
from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import set_committed_value

from superboss.core.actors import Actor, require_owner, require_project_actor
from superboss.core.errors import ConflictError, DomainError, ForbiddenError, NotFoundError
from superboss.core.security import utcnow
from superboss.modules.audit.schemas import AuditEventInput
from superboss.modules.audit.service import AuditService
from superboss.modules.finance.models import (
    CompanyMonthCost,
    CompanySetting,
    CreatedVia,
    FinanceAdjustment,
    FinanceEntry,
    FinanceImportBatch,
    FinanceImportRow,
    FinanceKind,
    FinancePayment,
    FinanceScope,
    FinanceVisibility,
    RewardAllocation,
)
from superboss.modules.finance.rewards import (
    PLACEHOLDER_SAME_DAY,
    next_payroll_date,
    reward_breakdown,
)
from superboss.modules.finance.schemas import (
    CompanyMonthCostRead,
    CompanyMonthCostWrite,
    CompanyTotals,
    FinanceAdjustmentCreate,
    FinanceAdjustmentRead,
    FinanceEntryCreate,
    FinanceEntryRead,
    FinanceImportCommand,
    FinanceImportRowRead,
    FinancePayCommand,
    FinancePaymentRead,
    FinanceSummary,
    ProjectTotals,
)
from superboss.modules.finance.schemas import (
    FinanceImportRow as FinanceImportRowWrite,
)
from superboss.modules.projects.models import Project
from superboss.modules.users.models import Role

REWARD_CATEGORIES = frozenset({"节余奖金", "执行部抽成", "团队抽成"})


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
    batch_key: str
    paid_on: date | None
    paid_cents: int | None
    voucher: str
    payments: list[FinancePayment]
    paid_total_cents: int
    unpaid_cents: int


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
        return False
    return False


def _payments_of(entry: FinanceEntry) -> list[FinancePayment]:
    if "payments" in entry.__dict__ and entry.payments:
        return list(entry.payments)
    return []


def paid_total_of(entry: FinanceEntry, amount_cents: int | None = None) -> tuple[int, date | None]:
    payments = _payments_of(entry)
    if payments:
        return sum(item.amount_cents for item in payments), max(item.paid_on for item in payments)
    return 0, None


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
    payments = _payments_of(entry)
    paid_total, last_paid = paid_total_of(entry, amount_cents)
    unpaid = max(amount_cents - paid_total, 0)
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
        batch_key=entry.batch_key,
        paid_on=last_paid,
        paid_cents=paid_total if paid_total else None,
        voucher=getattr(entry, "voucher", "") or "",
        payments=payments,
        paid_total_cents=paid_total,
        unpaid_cents=unpaid,
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
            batch_key=entry.batch_key,
            paid_on=entry.paid_on,
            paid_cents=entry.paid_cents,
            voucher=entry.voucher,
            paid_total_cents=entry.paid_total_cents,
            unpaid_cents=entry.unpaid_cents,
            payments=[FinancePaymentRead.model_validate(item) for item in entry.payments],
        )

    async def _load(self) -> list[EffectiveEntry]:
        rows = list(
            (
                await self.session.scalars(
                    select(FinanceEntry)
                    .options(
                        selectinload(FinanceEntry.adjustments),
                        selectinload(FinanceEntry.payments),
                    )
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
            batch_key="",
        )
        self.session.add(entry)
        await self.session.flush()
        set_committed_value(entry, "adjustments", [])
        set_committed_value(entry, "payments", [])
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
            .options(
                selectinload(FinanceEntry.adjustments),
                selectinload(FinanceEntry.payments),
            )
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

    async def rewards_for(self, actor: Actor, project_id: UUID) -> dict[str, object]:
        if actor.role == Role.STAFF:
            raise ForbiddenError()
        from superboss.modules.projects.models import Project

        project = await self.session.get(Project, project_id)
        if project is None:
            raise NotFoundError("PROJECT_NOT_FOUND", "Project not found")
        rows = list(
            (
                await self.session.scalars(
                    select(FinanceEntry)
                    .where(FinanceEntry.project_id == project_id)
                    .options(
                        selectinload(FinanceEntry.adjustments),
                        selectinload(FinanceEntry.payments),
                    )
                )
            ).all()
        )
        cost = 0
        received = 0
        last_paid = None
        for row in rows:
            effective = apply_adjustments(row)
            if not entry_is_visible(actor, effective.kind, effective.scope, effective.visibility):
                continue
            if effective.kind is FinanceKind.COST:
                if effective.category not in REWARD_CATEGORIES:
                    cost += effective.amount_cents
            else:
                received += effective.amount_cents
                if last_paid is None or effective.occurred_on > last_paid:
                    last_paid = effective.occurred_on
        payload = reward_breakdown(project.service_fee_cents or 0, cost)
        payload["received_cents"] = received
        payload["project_id"] = str(project.id)
        payload["project_name"] = project.name
        fee = project.service_fee_cents or 0
        same_day = await self._payroll_rule()
        payroll = next_payroll_date(last_paid, same_day) if fee and received >= fee else None
        payload["payroll_on"] = payroll.isoformat() if isinstance(payroll, date) else None
        payload["payroll_same_day"] = same_day
        payload["placeholder"] = True
        allocations = list(
            (
                await self.session.scalars(
                    select(RewardAllocation)
                    .where(RewardAllocation.project_id == project_id)
                    .order_by(RewardAllocation.kind, RewardAllocation.person_name)
                )
            ).all()
        )
        payload["allocations"] = [
            {
                "kind": item.kind,
                "person_name": item.person_name,
                "share_cents": item.share_cents,
                "is_placeholder": item.is_placeholder,
                "note": item.note,
            }
            for item in allocations
        ]
        return payload

    async def _payroll_rule(self) -> str:
        row = await self.session.get(CompanySetting, "payroll_same_day")
        if row is None:
            return PLACEHOLDER_SAME_DAY
        rule = row.value_json.get("rule") if isinstance(row.value_json, dict) else None
        return str(rule) if rule in {"THIS_MONTH", "NEXT_MONTH"} else PLACEHOLDER_SAME_DAY

    async def _opening(self) -> tuple[bool, int, str, bool]:
        row = await self.session.get(CompanySetting, "opening_balance")
        if row is None or not isinstance(row.value_json, dict):
            return False, 0, "", False
        raw_cents = row.value_json.get("cents") or 0
        cents = int(str(raw_cents))
        as_of = str(row.value_json.get("as_of") or "")
        return True, cents, as_of, bool(row.is_placeholder)

    async def overview(self, actor: Actor) -> dict[str, object]:
        if actor.role == Role.STAFF:
            raise ForbiddenError()
        require_project_actor(actor)
        from superboss.modules.directory.models import (
            CommunicationStatus,
            DirectoryCommunication,
        )
        from superboss.modules.projects.models import NodeStatus, Project

        projects = list(
            (await self.session.scalars(select(Project).options(selectinload(Project.nodes)))).all()
        )
        rows = list(
            (
                await self.session.scalars(
                    select(FinanceEntry).options(
                        selectinload(FinanceEntry.adjustments),
                        selectinload(FinanceEntry.payments),
                    )
                )
            ).all()
        )
        month_costs = {
            item.month: item.amount_cents
            for item in (await self.session.scalars(select(CompanyMonthCost))).all()
        }

        def _completed(project: Project) -> bool:
            if project.service_completed_on is not None:
                return True
            counted = [item for item in project.nodes if item.status.value != "SKIPPED"]
            if counted:
                return all(item.status is NodeStatus.DONE for item in counted)
            return False

        done = [item for item in projects if _completed(item)]
        fee_done = sum(item.service_fee_cents or 0 for item in done)
        received = 0
        paid = 0
        unpaid = 0
        by_month: dict[str, dict[str, int]] = {}
        project_received: dict[UUID, int] = {}
        project_cost: dict[UUID, int] = {}
        project_reward_paid: dict[UUID, dict[str, int]] = {}
        for row in rows:
            effective = apply_adjustments(row)
            if not entry_is_visible(actor, effective.kind, effective.scope, effective.visibility):
                continue
            month_key = f"{effective.occurred_on.year:04d}-{effective.occurred_on.month:02d}"
            bucket = by_month.setdefault(
                month_key,
                {
                    "cost_cents": 0,
                    "income_cents": 0,
                    "company_fixed_cents": 0,
                    "cash_paid_cents": 0,
                },
            )
            if effective.kind is FinanceKind.COST:
                if effective.category not in REWARD_CATEGORIES:
                    bucket["cost_cents"] += effective.amount_cents
                    if effective.scope is FinanceScope.COMPANY:
                        bucket["company_fixed_cents"] += effective.amount_cents
                    if effective.project_id is not None:
                        project_cost[effective.project_id] = (
                            project_cost.get(effective.project_id, 0) + effective.amount_cents
                        )
                elif effective.project_id is not None:
                    paid_rewards = project_reward_paid.setdefault(
                        effective.project_id, {"surplus": 0, "pool": 0}
                    )
                    if effective.category == "节余奖金":
                        paid_rewards["surplus"] += effective.paid_total_cents
                    else:
                        paid_rewards["pool"] += effective.paid_total_cents
                unpaid += effective.unpaid_cents
                paid += effective.paid_total_cents
                for payment in effective.payments:
                    pay_key = f"{payment.paid_on.year:04d}-{payment.paid_on.month:02d}"
                    pay_bucket = by_month.setdefault(
                        pay_key,
                        {
                            "cost_cents": 0,
                            "income_cents": 0,
                            "company_fixed_cents": 0,
                            "cash_paid_cents": 0,
                        },
                    )
                    pay_bucket["cash_paid_cents"] += payment.amount_cents
            else:
                bucket["income_cents"] += effective.amount_cents
                received += effective.amount_cents
                if effective.project_id is not None:
                    project_received[effective.project_id] = (
                        project_received.get(effective.project_id, 0) + effective.amount_cents
                    )
        for month_key, amount in month_costs.items():
            bucket = by_month.setdefault(
                month_key,
                {
                    "cost_cents": 0,
                    "income_cents": 0,
                    "company_fixed_cents": 0,
                    "cash_paid_cents": 0,
                },
            )
            bucket["company_fixed_cents"] = amount
        gross = 0
        for project in done:
            breakdown = reward_breakdown(
                project.service_fee_cents or 0, project_cost.get(project.id, 0)
            )
            gross += int(breakdown["gross_cents"])
        same_day = await self._payroll_rule()
        receivables: list[dict[str, object]] = []
        pending_rewards: list[dict[str, object]] = []
        for project in projects:
            fee = project.service_fee_cents or 0
            got = project_received.get(project.id, 0)
            if fee > got:
                receivables.append(
                    {
                        "project_id": str(project.id),
                        "project_name": project.name,
                        "fee_cents": fee,
                        "received_cents": got,
                        "outstanding_cents": fee - got,
                    }
                )
            if fee and got >= fee:
                breakdown = reward_breakdown(fee, project_cost.get(project.id, 0))
                already = project_reward_paid.get(project.id, {"surplus": 0, "pool": 0})
                surplus_left = max(int(breakdown["surplus_bonus_cents"]) - already["surplus"], 0)
                pool_left = max(int(breakdown["pool_pay_cents"]) - already["pool"], 0)
                last_income = None
                for row in rows:
                    effective = apply_adjustments(row)
                    if (
                        effective.project_id == project.id
                        and effective.kind is FinanceKind.INCOME
                        and (last_income is None or effective.occurred_on > last_income)
                    ):
                        last_income = effective.occurred_on
                payroll = next_payroll_date(last_income, same_day)
                if surplus_left or pool_left:
                    pending_rewards.append(
                        {
                            "project_id": str(project.id),
                            "project_name": project.name,
                            "surplus_bonus_cents": surplus_left,
                            "pool_pay_cents": pool_left,
                            "paid_surplus_cents": already["surplus"],
                            "paid_pool_cents": already["pool"],
                            "payroll_on": payroll.isoformat()
                            if isinstance(payroll, date)
                            else None,
                        }
                    )
        latest_status = (
            await self.session.execute(
                select(
                    DirectoryCommunication.entry_id,
                    DirectoryCommunication.status,
                ).order_by(DirectoryCommunication.occurred_on.desc())
            )
        ).all()
        seen: set[UUID] = set()
        commissioned = 0
        talking = 0
        for entry_id, status in latest_status:
            if entry_id in seen:
                continue
            seen.add(entry_id)
            if status is CommunicationStatus.COMMISSIONED:
                commissioned += 1
            elif status in {CommunicationStatus.LEARNING, CommunicationStatus.CONTACTING}:
                talking += 1
        months = [
            {
                "month": key,
                "cost_cents": value["cost_cents"],
                "income_cents": value["income_cents"],
                "company_fixed_cents": value["company_fixed_cents"],
                "cash_paid_cents": value.get("cash_paid_cents", 0),
            }
            for key, value in sorted(by_month.items())
        ]
        has_opening, opening_cents, opening_as_of, opening_placeholder = await self._opening()
        net = received - paid
        return {
            "active_count": sum(1 for item in projects if not _completed(item)),
            "completed_count": len(done),
            "completed_fee_cents": fee_done,
            "completed_gross_cents": gross,
            "received_cents": received,
            "paid_cents": paid,
            "unpaid_cents": unpaid,
            "net_inflow_cents": net,
            "has_opening_balance": has_opening,
            "opening_balance_cents": opening_cents if has_opening else 0,
            "opening_as_of": opening_as_of,
            "available_cents": opening_cents + net if has_opening else None,
            "placeholder": (not has_opening) or opening_placeholder,
            "payroll_same_day": same_day,
            "months": months,
            "receivables": receivables,
            "pending_rewards": pending_rewards,
            "pipeline": {"commissioned": commissioned, "talking": talking},
        }

    def _fingerprint(self, entry: EffectiveEntry | FinanceEntry) -> str:
        amount = getattr(entry, "amount_cents", 0)
        return "|".join(
            [
                entry.kind.value if hasattr(entry.kind, "value") else str(entry.kind),
                entry.scope.value if hasattr(entry.scope, "value") else str(entry.scope),
                str(entry.project_id or ""),
                entry.occurred_on.isoformat(),
                str(amount),
                entry.category,
            ]
        )

    @staticmethod
    def _memo_key(value: object) -> str:
        return re.sub(r"\s+", "", str(value or "")).lower()

    def _source_row(self, row: FinanceImportRowWrite, index: int) -> int:
        source = row.source_row
        if isinstance(source, int) and source > 0:
            return source
        return index + 1

    def _source_sheet(self, row: FinanceImportRowWrite) -> str:
        sheet = (row.source_sheet or "").strip()
        return sheet or "Sheet1"

    @staticmethod
    def _payload_source_row(payload: dict[str, object]) -> int | None:
        raw = payload.get("source_row")
        if isinstance(raw, bool):
            return None
        if isinstance(raw, int) and raw > 0:
            return raw
        if isinstance(raw, str) and raw.isdigit() and int(raw) > 0:
            return int(raw)
        return None

    @staticmethod
    def _payload_source_sheet(payload: dict[str, object]) -> str:
        sheet = str(payload.get("source_sheet") or "").strip()
        return sheet or "Sheet1"

    def _project_key(
        self,
        project_id: object,
        project_name: object,
        by_name: dict[str, Project],
    ) -> str:
        if project_id:
            return str(project_id)
        match = by_name.get(str(project_name or ""))
        if match is not None:
            return str(match.id)
        return ""

    def _incoming_content(
        self, row: FinanceImportRowWrite, by_name: dict[str, Project]
    ) -> dict[str, object]:
        return {
            "amount_cents": row.amount_cents,
            "occurred_on": row.occurred_on.isoformat(),
            "category": row.category,
            "memo": self._memo_key(row.memo),
            "kind": row.kind.value,
            "scope": row.scope.value,
            "project_id": self._project_key(row.project_id, row.project_name, by_name),
            "voucher": (row.voucher or "").strip(),
        }

    def _stored_content(
        self, payload: dict[str, object], by_name: dict[str, Project]
    ) -> dict[str, object]:
        occurred = payload.get("occurred_on")
        return {
            "amount_cents": payload.get("amount_cents"),
            "occurred_on": str(occurred) if occurred is not None else "",
            "category": payload.get("category"),
            "memo": self._memo_key(payload.get("memo")),
            "kind": payload.get("kind"),
            "scope": payload.get("scope"),
            "project_id": self._project_key(
                payload.get("project_id"), payload.get("project_name"), by_name
            ),
            "voucher": str(payload.get("voucher") or "").strip(),
        }

    @staticmethod
    def _success_locked(row: FinanceImportRow) -> bool:
        if row.status in {"INSERTED", "LINKED"}:
            return True
        return row.reason == "SUCCESS_CONTENT_MISMATCH" and row.entry_id is not None

    async def _align_legacy_import_indexes(
        self, rows: list[FinanceImportRow]
    ) -> list[FinanceImportRow]:
        occupied = {item.row_index: item for item in rows}
        by_target: dict[int, list[FinanceImportRow]] = {}
        for item in rows:
            payload = dict(item.payload or {})
            source_row = self._payload_source_row(payload)
            if source_row is None or source_row == item.row_index:
                continue
            by_target.setdefault(source_row, []).append(item)
        movers: list[tuple[FinanceImportRow, int]] = []
        for source_row, group in by_target.items():
            unique = {item.id: item for item in group}
            if len(unique) != 1:
                continue
            item = next(iter(unique.values()))
            occupant = occupied.get(source_row)
            if occupant is not None and occupant is not item:
                continue
            movers.append((item, source_row))
        if not movers:
            return rows
        used_temp: set[int] = set(occupied)
        for item, _source_row in movers:
            temp = -1 - item.row_index
            while temp in used_temp:
                temp -= 1
            occupied.pop(item.row_index, None)
            item.row_index = temp
            used_temp.add(temp)
            occupied[temp] = item
        await self.session.flush()
        for item, source_row in movers:
            occupied.pop(item.row_index, None)
            item.row_index = source_row
            occupied[source_row] = item
        await self.session.flush()
        return rows

    def _prior_matches(
        self,
        prior_rows: list[FinanceImportRow],
        source_row: int,
        source_sheet: str,
    ) -> list[FinanceImportRow]:
        payload_matches: list[FinanceImportRow] = []
        index_matches: list[FinanceImportRow] = []
        for item in prior_rows:
            payload = dict(item.payload or {})
            stored_row = self._payload_source_row(payload)
            stored_sheet = self._payload_source_sheet(payload)
            if stored_row == source_row and stored_sheet == source_sheet:
                payload_matches.append(item)
            elif stored_row is None and item.row_index == source_row:
                index_matches.append(item)
        matches = payload_matches or index_matches
        unique = {item.id: item for item in matches}
        return list(unique.values())

    async def _ensure_import_batch(self, actor: Actor, batch_key: str) -> FinanceImportBatch:
        batch = await self.session.get(FinanceImportBatch, batch_key)
        if batch is None:
            batch = FinanceImportBatch(batch_key=batch_key, created_by=actor.subject_id)
            self.session.add(batch)
            await self.session.flush()
        return batch

    async def import_batch(
        self, actor: Actor, command: FinanceImportCommand, request_id: UUID | None = None
    ) -> dict[str, object]:
        require_owner(actor)
        batch = await self._ensure_import_batch(actor, command.batch_key)
        loaded_prior = list(
            (
                await self.session.scalars(
                    select(FinanceImportRow).where(FinanceImportRow.batch_key == command.batch_key)
                )
            ).all()
        )
        loaded_prior = await self._align_legacy_import_indexes(loaded_prior)
        replayed = bool(loaded_prior)
        known: dict[str, list[EffectiveEntry | FinanceEntry]] = {}
        for loaded in await self._load():
            known.setdefault(self._fingerprint(loaded), []).append(loaded)
        projects = list((await self.session.scalars(select(Project))).all())
        by_name = {item.name: item for item in projects}
        unresolved: list[dict[str, object]] = []
        created: list[FinanceEntry] = []
        skipped = 0
        pending_inserts: list[tuple[int, FinanceEntry, str, dict[str, object]]] = []
        for index, row in enumerate(command.rows):
            source_row = self._source_row(row, index)
            source_sheet = self._source_sheet(row)
            matches = self._prior_matches(loaded_prior, source_row, source_sheet)
            if len(matches) > 1:
                item = {
                    "row": source_row,
                    "source_row": source_row,
                    "source_sheet": source_sheet,
                    "reason": "SOURCE_IDENTITY_CONFLICT",
                    "project_name": row.project_name,
                    "amount_cents": row.amount_cents,
                    "kind": row.kind.value,
                    "scope": row.scope.value,
                    "occurred_on": row.occurred_on.isoformat(),
                    "category": row.category,
                    "memo": row.memo,
                    "voucher": row.voucher,
                    "conflicting_row_indexes": [prior.row_index for prior in matches],
                }
                unresolved.append(item)
                for prior in matches:
                    payload = dict(prior.payload or {})
                    payload["source_identity_conflict"] = True
                    payload["conflicting_row_indexes"] = item["conflicting_row_indexes"]
                    payload["reason"] = "SOURCE_IDENTITY_CONFLICT"
                    await self._upsert_import_row(
                        batch.batch_key,
                        prior.row_index,
                        "UNRESOLVED",
                        "SOURCE_IDENTITY_CONFLICT",
                        prior.fingerprint,
                        prior.entry_id,
                        payload,
                        overwrite_success=True,
                    )
                continue
            previous = matches[0] if matches else None
            if previous is not None and self._success_locked(previous):
                incoming = self._incoming_content(row, by_name)
                stored = self._stored_content(dict(previous.payload or {}), by_name)
                if incoming == stored:
                    if previous.status == "UNRESOLVED":
                        previous.status = "INSERTED"
                        previous.reason = ""
                    skipped += 1
                    continue
                original = dict(previous.payload or {})
                item = {
                    "row": source_row,
                    "source_row": source_row,
                    "source_sheet": source_sheet,
                    "reason": "SUCCESS_CONTENT_MISMATCH",
                    "project_name": row.project_name,
                    "amount_cents": row.amount_cents,
                    "kind": row.kind.value,
                    "scope": row.scope.value,
                    "occurred_on": row.occurred_on.isoformat(),
                    "category": row.category,
                    "memo": row.memo,
                    "voucher": row.voucher,
                    "existing_entry_id": str(previous.entry_id)
                    if previous.entry_id
                    else original.get("existing_entry_id"),
                    "existing_amount_cents": original.get("amount_cents"),
                    "existing_memo": original.get("memo"),
                }
                unresolved.append(item)
                stored_payload = {
                    **original,
                    "pending_amount_cents": row.amount_cents,
                    "pending_occurred_on": row.occurred_on.isoformat(),
                    "pending_category": row.category,
                    "pending_memo": row.memo,
                    "pending_kind": row.kind.value,
                    "pending_scope": row.scope.value,
                    "pending_project_name": row.project_name,
                    "pending_voucher": row.voucher,
                    "reason": "SUCCESS_CONTENT_MISMATCH",
                    "existing_entry_id": item["existing_entry_id"],
                    "existing_amount_cents": original.get("amount_cents"),
                    "existing_memo": original.get("memo"),
                    "source_row": original.get("source_row", source_row),
                    "source_sheet": original.get("source_sheet", source_sheet),
                }
                await self._upsert_import_row(
                    batch.batch_key,
                    previous.row_index,
                    "UNRESOLVED",
                    "SUCCESS_CONTENT_MISMATCH",
                    previous.fingerprint,
                    previous.entry_id,
                    stored_payload,
                    overwrite_success=True,
                )
                continue
            project_id = row.project_id
            if row.scope is FinanceScope.PROJECT and project_id is None:
                match = by_name.get(row.project_name)
                if match is None:
                    item = {
                        "row": source_row,
                        "source_row": source_row,
                        "source_sheet": row.source_sheet or "Sheet1",
                        "reason": "PROJECT_UNRESOLVED",
                        "project_name": row.project_name,
                        "amount_cents": row.amount_cents,
                        "kind": row.kind.value,
                        "scope": row.scope.value,
                        "occurred_on": row.occurred_on.isoformat(),
                        "category": row.category,
                        "memo": row.memo,
                        "voucher": row.voucher,
                    }
                    unresolved.append(item)
                    await self._upsert_import_row(
                        batch.batch_key,
                        source_row,
                        "UNRESOLVED",
                        "PROJECT_UNRESOLVED",
                        "",
                        None,
                        item,
                    )
                    continue
                project_id = match.id
            probe = FinanceEntry(
                kind=row.kind,
                scope=row.scope,
                project_id=project_id,
                amount_cents=row.amount_cents,
                occurred_on=row.occurred_on,
                category=row.category,
                memo=row.memo,
                voucher=row.voucher,
                visibility=default_visibility(row.kind, row.scope),
                created_by=actor.subject_id,
                created_via=CreatedVia.FORM,
                batch_key=command.batch_key,
            )
            mark = self._fingerprint(probe)
            existing_list = known.get(mark, [])
            same_memo = [
                item
                for item in existing_list
                if self._memo_key(item.memo) == self._memo_key(row.memo)
            ]
            if same_memo:
                skipped += 1
                candidate = same_memo[0]
                item = {
                    "row": source_row,
                    "source_row": source_row,
                    "source_sheet": row.source_sheet or "Sheet1",
                    "reason": "DUPLICATE_CANDIDATE",
                    "fingerprint": mark,
                    "project_name": row.project_name,
                    "project_id": str(project_id) if project_id else None,
                    "amount_cents": row.amount_cents,
                    "kind": row.kind.value,
                    "scope": row.scope.value,
                    "occurred_on": row.occurred_on.isoformat(),
                    "category": row.category,
                    "memo": row.memo,
                    "voucher": row.voucher,
                    "candidate_entry_id": str(candidate.id),
                    "candidate_entry_ids": [str(entry.id) for entry in same_memo],
                    "candidate_memo": candidate.memo,
                    "candidate_voucher": getattr(candidate, "voucher", "") or "",
                }
                unresolved.append(item)
                await self._upsert_import_row(
                    batch.batch_key,
                    source_row,
                    "UNRESOLVED",
                    "DUPLICATE_CANDIDATE",
                    mark,
                    None,
                    item,
                )
                continue
            self.session.add(probe)
            created.append(probe)
            known.setdefault(mark, []).append(probe)
            pending_inserts.append(
                (
                    source_row,
                    probe,
                    mark,
                    {
                        "row": source_row,
                        "source_row": source_row,
                        "source_sheet": row.source_sheet or "Sheet1",
                        "project_name": row.project_name,
                        "project_id": str(project_id) if project_id else None,
                        "amount_cents": row.amount_cents,
                        "kind": row.kind.value,
                        "scope": row.scope.value,
                        "occurred_on": row.occurred_on.isoformat(),
                        "category": row.category,
                        "memo": row.memo,
                        "voucher": row.voucher,
                    },
                )
            )
        await self.session.flush()
        for source_row, probe, mark, payload in pending_inserts:
            linked = {**payload, "entry_id": str(probe.id)}
            await self._upsert_import_row(
                batch.batch_key, source_row, "INSERTED", "", mark, probe.id, linked
            )
        for created_entry in created:
            set_committed_value(created_entry, "adjustments", [])
            set_committed_value(created_entry, "payments", [])
            await self._record(
                actor,
                "finance.entry.import",
                request_id,
                created_entry,
                {"batch_key": command.batch_key, "amount_cents": created_entry.amount_cents},
            )
        names = await self._projects(
            {created_entry.project_id for created_entry in created if created_entry.project_id}
        )
        return {
            "batch_key": command.batch_key,
            "inserted": len(created),
            "skipped": skipped,
            "unresolved": unresolved,
            "replayed": replayed,
            "entries": [
                self._to_read(apply_adjustments(item), names).model_dump(mode="json")
                for item in created
            ],
        }

    async def _upsert_import_row(
        self,
        batch_key: str,
        row_index: int,
        status: str,
        reason: str,
        fingerprint: str,
        entry_id: UUID | None,
        payload: dict[str, object],
        overwrite_success: bool = False,
    ) -> None:
        existing = await self.session.scalar(
            select(FinanceImportRow).where(
                FinanceImportRow.batch_key == batch_key, FinanceImportRow.row_index == row_index
            )
        )
        if existing is None:
            self.session.add(
                FinanceImportRow(
                    batch_key=batch_key,
                    row_index=row_index,
                    status=status,
                    reason=reason,
                    fingerprint=fingerprint,
                    entry_id=entry_id,
                    payload=payload,
                )
            )
            return
        if self._success_locked(existing) and status == "UNRESOLVED" and not overwrite_success:
            # Parse failures must not erase a previously successful source identity.
            return
        existing.status = status
        existing.reason = reason
        existing.fingerprint = fingerprint
        existing.entry_id = entry_id
        existing.payload = payload

    async def mark_paid(
        self,
        actor: Actor,
        entry_id: UUID,
        command: FinancePayCommand,
        request_id: UUID | None = None,
    ) -> FinanceEntryRead:
        require_owner(actor)
        entry = await self.session.scalar(
            select(FinanceEntry).where(FinanceEntry.id == entry_id).with_for_update()
        )
        if entry is None:
            raise NotFoundError("FINANCE_ENTRY_NOT_FOUND", "Finance entry not found")
        await self.session.refresh(entry, attribute_names=["adjustments", "payments"])
        effective = apply_adjustments(entry)
        key = (command.idempotency_key or "").strip()
        if key:
            duplicate = next(
                (item for item in effective.payments if item.idempotency_key == key), None
            )
            if duplicate is not None:
                if command.paid_cents is not None and command.paid_cents != duplicate.amount_cents:
                    raise ConflictError(
                        "FINANCE_PAY_KEY_MISMATCH",
                        "Idempotency key already recorded a different payment amount",
                    )
                if command.paid_on != duplicate.paid_on:
                    raise ConflictError(
                        "FINANCE_PAY_KEY_MISMATCH",
                        "Idempotency key already recorded a different payment date",
                    )
                names = await self._projects({entry.project_id} if entry.project_id else set())
                return self._to_read(effective, names)
        remaining = effective.unpaid_cents
        if remaining <= 0:
            raise ConflictError("FINANCE_ALREADY_PAID", "This entry is already paid in full")
        requested = command.paid_cents
        if requested is not None and requested > remaining:
            raise ConflictError(
                "FINANCE_AMOUNT_EXCEEDS", "Payment exceeds the remaining unpaid amount"
            )
        amount = remaining if requested is None else requested
        payment = FinancePayment(
            entry_id=entry.id,
            paid_on=command.paid_on,
            amount_cents=amount,
            idempotency_key=key,
            created_by=actor.subject_id,
        )
        self.session.add(payment)
        try:
            await self.session.flush()
        except IntegrityError as error:
            raise ConflictError("FINANCE_PAY_CONFLICT", "Payment could not be recorded") from error
        entry.payments.append(payment)
        total, last_paid = paid_total_of(entry)
        entry.paid_cents = total
        entry.paid_on = last_paid
        await self.session.flush()
        await self._record(
            actor,
            "finance.entry.pay",
            request_id,
            entry,
            {"paid_on": command.paid_on.isoformat(), "paid_cents": amount},
        )
        names = await self._projects({entry.project_id} if entry.project_id else set())
        return self._to_read(apply_adjustments(entry), names)

    async def import_from_file(
        self,
        actor: Actor,
        payload: bytes,
        *,
        batch_key: str,
        filename: str = "",
        request_id: UUID | None = None,
        source_file_id: UUID | None = None,
    ) -> dict[str, object]:
        from superboss.modules.finance.xlsx import map_finance_rows

        rows, parse_unresolved = map_finance_rows(payload)
        result = await self.import_batch(
            actor,
            FinanceImportCommand(batch_key=batch_key, rows=rows),
            request_id,
        )
        for raw in parse_unresolved:
            if not isinstance(raw, dict):
                continue
            row_no = raw.get("row")
            if not isinstance(row_no, int) or row_no < 1:
                continue
            row_payload: dict[str, object] = {
                "row": row_no,
                "source_row": row_no,
                "source_sheet": str(raw.get("source_sheet") or "Sheet1"),
                "reason": str(raw.get("reason") or "ROW_INCOMPLETE"),
            }
            for key in (
                "project_name",
                "amount_text",
                "kind_text",
                "occurred_text",
                "category",
                "memo",
                "voucher",
                "raw",
            ):
                if key in raw:
                    row_payload[key] = raw[key]
            await self._upsert_import_row(
                batch_key,
                row_no,
                "UNRESOLVED",
                str(raw.get("reason") or "ROW_INCOMPLETE"),
                "",
                None,
                row_payload,
            )
        prior_unresolved = result.get("unresolved")
        merged: list[object] = []
        if isinstance(prior_unresolved, list):
            merged.extend(prior_unresolved)
        merged.extend(parse_unresolved)
        result["unresolved"] = merged
        if source_file_id is not None:
            raw_entries = result.get("entries")
            entries = raw_entries if isinstance(raw_entries, list) else []
            created_ids = [
                UUID(str(item["id"]))
                for item in entries
                if isinstance(item, dict) and item.get("id")
            ]
            if created_ids:
                stored = (
                    await self.session.scalars(
                        select(FinanceEntry).where(FinanceEntry.id.in_(created_ids))
                    )
                ).all()
                for entry in stored:
                    if entry.voucher_file_id is None:
                        entry.voucher_file_id = source_file_id
                await self.session.flush()
        result["parse_unresolved"] = parse_unresolved
        result["source_filename"] = filename
        result["source_file_id"] = str(source_file_id) if source_file_id else None
        return result

    async def list_import_rows(
        self,
        actor: Actor,
        *,
        status: str | None = "UNRESOLVED",
        offset: int = 0,
        limit: int = 100,
    ) -> tuple[list[FinanceImportRow], int]:
        require_owner(actor)
        statement = select(FinanceImportRow)
        count_statement = select(func.count()).select_from(FinanceImportRow)
        if status:
            statement = statement.where(FinanceImportRow.status == status)
            count_statement = count_statement.where(FinanceImportRow.status == status)
        total = int(await self.session.scalar(count_statement) or 0)
        rows = (
            await self.session.scalars(
                statement.order_by(FinanceImportRow.batch_key, FinanceImportRow.row_index)
                .offset(offset)
                .limit(limit)
            )
        ).all()
        return list(rows), total

    async def resolve_import_row(
        self,
        actor: Actor,
        batch_key: str,
        row_index: int,
        action: str,
        entry_id: UUID | None = None,
        request_id: UUID | None = None,
    ) -> FinanceImportRowRead:
        require_owner(actor)
        row = await self.session.scalar(
            select(FinanceImportRow)
            .where(FinanceImportRow.batch_key == batch_key, FinanceImportRow.row_index == row_index)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise NotFoundError("FINANCE_IMPORT_ROW_NOT_FOUND", "Import row not found")
        await self.session.refresh(row)
        payload = dict(row.payload or {})
        if row.status in {"INSERTED", "LINKED"} and row.entry_id is not None:
            return FinanceImportRowRead.model_validate(row)
        if (
            row.reason == "SUCCESS_CONTENT_MISMATCH"
            and row.entry_id is not None
            and action == "insert_independent"
        ):
            raise ConflictError(
                "FINANCE_SUCCESS_CONTENT_MISMATCH",
                "Original successful entry is retained pending confirmation",
            )
        if action == "link_voucher":
            target_id = entry_id
            if target_id is None:
                raw = payload.get("candidate_entry_id")
                if raw:
                    target_id = UUID(str(raw))
            if target_id is None:
                raise DomainError("FINANCE_CANDIDATE_REQUIRED", "Candidate entry is required", 422)
            entry = await self.session.scalar(
                select(FinanceEntry).where(FinanceEntry.id == target_id).with_for_update()
            )
            if entry is None:
                raise NotFoundError("FINANCE_ENTRY_NOT_FOUND", "Finance entry not found")
            voucher = str(payload.get("voucher") or "").strip()
            existing_voucher = (entry.voucher or "").strip()
            if voucher and existing_voucher and voucher != existing_voucher:
                raise ConflictError(
                    "FINANCE_VOUCHER_CONFLICT",
                    "Target entry already has a different voucher",
                )
            if voucher and not existing_voucher:
                entry.voucher = voucher[:255]
            row.status = "LINKED"
            row.entry_id = entry.id
            payload["entry_id"] = str(entry.id)
            row.payload = payload
            await self.session.flush()
            return FinanceImportRowRead.model_validate(row)
        if action == "insert_independent":
            kind = str(payload.get("kind") or "COST")
            scope = str(payload.get("scope") or "PROJECT")
            occurred = payload.get("occurred_on")
            amount = payload.get("amount_cents")
            if not isinstance(amount, int) or amount < 1 or not occurred:
                raise DomainError("FINANCE_IMPORT_INCOMPLETE", "Import row cannot be inserted", 422)
            project_raw = payload.get("project_id")
            project_id = UUID(str(project_raw)) if project_raw else None
            if project_id is None and payload.get("project_name"):
                match = await self.session.scalar(
                    select(Project).where(Project.name == str(payload.get("project_name")))
                )
                if match is None:
                    raise DomainError("PROJECT_UNRESOLVED", "Project could not be resolved", 422)
                project_id = match.id
            probe = FinanceEntry(
                kind=FinanceKind(kind),
                scope=FinanceScope(scope),
                project_id=project_id,
                amount_cents=amount,
                occurred_on=date.fromisoformat(str(occurred))
                if not isinstance(occurred, date)
                else occurred,
                category=str(payload.get("category") or "未分类")[:64],
                memo=str(payload.get("memo") or ""),
                voucher=str(payload.get("voucher") or ""),
                visibility=default_visibility(FinanceKind(kind), FinanceScope(scope)),
                created_by=actor.subject_id,
                created_via=CreatedVia.FORM,
                batch_key=batch_key,
            )
            self.session.add(probe)
            await self.session.flush()
            set_committed_value(probe, "adjustments", [])
            set_committed_value(probe, "payments", [])
            row.status = "INSERTED"
            row.entry_id = probe.id
            payload["entry_id"] = str(probe.id)
            row.payload = payload
            await self._record(
                actor,
                "finance.entry.import",
                request_id,
                probe,
                {"batch_key": batch_key, "amount_cents": probe.amount_cents, "resolved": True},
            )
            await self.session.flush()
            return FinanceImportRowRead.model_validate(row)
        raise DomainError("FINANCE_IMPORT_ACTION", "Unknown import resolve action", 422)

    async def list_month_costs(self, actor: Actor) -> list[CompanyMonthCostRead]:
        if actor.role == Role.STAFF:
            raise ForbiddenError()
        require_project_actor(actor)
        rows = list(
            (
                await self.session.scalars(
                    select(CompanyMonthCost).order_by(CompanyMonthCost.month)
                )
            ).all()
        )
        return [
            CompanyMonthCostRead(month=item.month, amount_cents=item.amount_cents) for item in rows
        ]

    async def upsert_month_cost(
        self, actor: Actor, month: str, command: CompanyMonthCostWrite
    ) -> CompanyMonthCostRead:
        require_owner(actor)
        if len(month) != 7 or month[4] != "-":
            raise DomainError("VALIDATION_ERROR", "month must be YYYY-MM", 422)
        row = await self.session.get(CompanyMonthCost, month)
        if row is None:
            row = CompanyMonthCost(month=month, amount_cents=command.amount_cents)
            self.session.add(row)
        else:
            row.amount_cents = command.amount_cents
        await self.session.flush()
        return CompanyMonthCostRead(month=row.month, amount_cents=row.amount_cents)
