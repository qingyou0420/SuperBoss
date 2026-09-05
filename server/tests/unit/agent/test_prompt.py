"""SOUL prompt assembly and card payload validation."""

from superboss.modules.agent.cards import parse_card_payload
from superboss.modules.agent.models import CardKind
from superboss.modules.agent.schemas import FinanceEntryCard
from superboss.modules.agent.soul import DEFAULT_SOUL, SYSTEM_CONSTRAINTS, assemble_system_prompt
from superboss.modules.finance.models import FinanceKind, FinanceScope


def test_system_prompt_puts_constraints_before_soul_and_memory() -> None:
    prompt = assemble_system_prompt(DEFAULT_SOUL, "- 项目名用中文", "谈过房租")
    assert prompt.index(SYSTEM_CONSTRAINTS[:12]) == 0
    assert "项目名用中文" in prompt
    assert "谈过房租" in prompt
    assert "不得声称已经入库" in SYSTEM_CONSTRAINTS or "不得声称已经入库" in prompt


def test_finance_card_payload_is_strict() -> None:
    parsed = parse_card_payload(
        CardKind.FINANCE_ENTRY,
        {
            "kind": "COST",
            "scope": "COMPANY",
            "amount_cents": 800_000,
            "occurred_on": "2026-09-01",
            "category": "房租",
        },
    )
    assert isinstance(parsed, FinanceEntryCard)
    assert parsed.kind is FinanceKind.COST
    assert parsed.scope is FinanceScope.COMPANY
