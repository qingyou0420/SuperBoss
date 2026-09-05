"""Finance visibility is role-based and independent of project membership."""

from datetime import UTC, date, datetime
from uuid import uuid4

from superboss.core.actors import Actor
from superboss.modules.finance.models import (
    CreatedVia,
    FinanceAdjustment,
    FinanceEntry,
    FinanceKind,
    FinanceScope,
    FinanceVisibility,
)
from superboss.modules.finance.service import (
    apply_adjustments,
    default_visibility,
    entry_is_visible,
    month_contains,
)
from superboss.modules.users.models import Role


def test_default_visibility_follows_scope_and_kind() -> None:
    assert default_visibility(FinanceKind.COST, FinanceScope.PROJECT) is FinanceVisibility.ALL
    assert (
        default_visibility(FinanceKind.COST, FinanceScope.COMPANY) is FinanceVisibility.MANAGEMENT
    )
    assert (
        default_visibility(FinanceKind.INCOME, FinanceScope.PROJECT) is FinanceVisibility.MANAGEMENT
    )


def test_staff_sees_only_shared_project_costs() -> None:
    staff = Actor(uuid4(), Role.STAFF)
    assert entry_is_visible(staff, FinanceKind.COST, FinanceScope.PROJECT, FinanceVisibility.ALL)
    assert not entry_is_visible(
        staff, FinanceKind.COST, FinanceScope.PROJECT, FinanceVisibility.MANAGEMENT
    )
    assert not entry_is_visible(
        staff, FinanceKind.COST, FinanceScope.COMPANY, FinanceVisibility.ALL
    )
    assert not entry_is_visible(
        staff, FinanceKind.INCOME, FinanceScope.PROJECT, FinanceVisibility.ALL
    )


def test_manager_sees_published_company_and_project_rows() -> None:
    manager = Actor(uuid4(), Role.MANAGER)
    assert entry_is_visible(
        manager, FinanceKind.INCOME, FinanceScope.COMPANY, FinanceVisibility.MANAGEMENT
    )
    assert not entry_is_visible(
        manager, FinanceKind.COST, FinanceScope.COMPANY, FinanceVisibility.OWNER_ONLY
    )


def test_apply_adjustments_keeps_the_original_row() -> None:
    entry = FinanceEntry(
        kind=FinanceKind.COST,
        scope=FinanceScope.COMPANY,
        amount_cents=800_000,
        occurred_on=date(2026, 9, 1),
        category="房租",
        visibility=FinanceVisibility.MANAGEMENT,
        created_by=uuid4(),
        created_via=CreatedVia.FORM,
        created_at=datetime(2026, 9, 1, tzinfo=UTC),
    )
    entry.adjustments = [
        FinanceAdjustment(
            field="amount_cents",
            old_value="800000",
            new_value="900000",
            reason="补差",
            created_by=uuid4(),
            created_at=datetime(2026, 9, 2, tzinfo=UTC),
        )
    ]
    effective = apply_adjustments(entry)
    assert entry.amount_cents == 800_000
    assert effective.amount_cents == 900_000
    assert month_contains(effective.occurred_on, "2026-09")
    assert not month_contains(effective.occurred_on, "2026-10")
