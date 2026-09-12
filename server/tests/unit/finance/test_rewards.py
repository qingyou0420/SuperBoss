from datetime import date

from superboss.modules.finance.rewards import next_payroll_date, reward_breakdown


def test_reward_examples_for_thirty_thousand_fee() -> None:
    fee = 3_000_000
    low = reward_breakdown(fee, 400_000)
    assert low["surplus_bonus_cents"] == 200_000
    assert low["pool_pay_cents"] == 300_000
    assert low["gross_cents"] == 2_100_000
    mid = reward_breakdown(fee, 700_000)
    assert mid["surplus_bonus_cents"] == 0
    assert mid["pool_pay_cents"] == 200_000
    assert mid["gross_cents"] == 2_100_000
    high = reward_breakdown(fee, 1_000_000)
    assert high["surplus_bonus_cents"] == 0
    assert high["pool_pay_cents"] == 0
    assert high["leftover_overrun_cents"] == 100_000
    assert high["gross_cents"] == 2_000_000


def test_payroll_date_placeholder_puts_same_day_in_this_month() -> None:
    assert next_payroll_date(date(2026, 9, 10)) == date(2026, 9, 20)
    assert next_payroll_date(date(2026, 9, 21)) == date(2026, 10, 20)
    assert next_payroll_date(date(2026, 9, 20)) == date(2026, 9, 20)
    assert next_payroll_date(date(2026, 9, 20), "NEXT_MONTH") == date(2026, 10, 20)
    assert next_payroll_date(date(2026, 9, 20), "UNDECIDED") is None
