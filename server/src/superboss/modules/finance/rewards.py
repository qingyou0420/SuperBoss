"""Project reward math from confirmed service fee F and direct cost C."""

from calendar import monthrange
from datetime import date
from typing import Any

# Placeholder until the boss confirms same-day-20th cutover.
PLACEHOLDER_SAME_DAY = "THIS_MONTH"


def reward_breakdown(fee_cents: int, cost_cents: int) -> dict[str, Any]:
    fee = max(fee_cents, 0)
    cost = max(cost_cents, 0)
    cap = fee * 20 // 100
    pool = fee * 10 // 100
    surplus = max(cap - cost, 0)
    overrun = max(cost - cap, 0)
    pool_cut = min(overrun, pool)
    pool_pay = pool - pool_cut
    leftover = max(cost - fee * 30 // 100, 0)
    gross = fee - cost - surplus - pool_pay
    return {
        "fee_cents": fee,
        "cost_cents": cost,
        "cost_cap_cents": cap,
        "surplus_bonus_cents": surplus,
        "overrun_cents": overrun,
        "pool_cut_cents": pool_cut,
        "pool_pay_cents": pool_pay,
        "leftover_overrun_cents": leftover,
        "gross_cents": gross,
        "provisional": fee == 0,
    }


def _shift_month(value: date, delta: int) -> date:
    month = value.month + delta
    year = value.year
    while month > 12:
        month -= 12
        year += 1
    while month < 1:
        month += 12
        year -= 1
    last = monthrange(year, month)[1]
    return date(year, month, min(20, last))


def next_payroll_date(paid_on: object, same_day: str = PLACEHOLDER_SAME_DAY) -> date | None:
    """Tail payment on day D settles on the next 20th.

    Same-day 20th uses the placeholder THIS_MONTH rule until replaced.
    """
    if not isinstance(paid_on, date):
        return None
    if paid_on.day < 20:
        return date(paid_on.year, paid_on.month, 20)
    if paid_on.day == 20:
        if same_day == "THIS_MONTH":
            return date(paid_on.year, paid_on.month, 20)
        if same_day == "NEXT_MONTH":
            return _shift_month(paid_on, 1)
        return None
    return _shift_month(paid_on, 1)
