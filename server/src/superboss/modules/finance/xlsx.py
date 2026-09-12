"""Map a finance workbook into import rows. Amounts are yuan."""

from __future__ import annotations

from datetime import date
from typing import Any

from superboss.modules.directory.xlsx import as_date, read_sheet_rows
from superboss.modules.finance.models import FinanceKind, FinanceScope
from superboss.modules.finance.schemas import FinanceImportRow


def _first(record: dict[str, Any], *names: str) -> str:
    for name in names:
        value = record.get(name)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _cents(value: str) -> int | None:
    cleaned = value.replace(",", "").replace("￥", "").replace("¥", "").replace("元", "")
    if not cleaned:
        return None
    try:
        yuan = float(cleaned)
    except ValueError:
        return None
    cents = round(yuan * 100)
    if cents < 1:
        return None
    return cents


def _kind(value: str) -> FinanceKind | None:
    text = value.strip().upper()
    if text in {"COST", "成本", "支出", "费用"}:
        return FinanceKind.COST
    if text in {"INCOME", "收入", "回款", "收款"}:
        return FinanceKind.INCOME
    return None


def _scope(value: str) -> FinanceScope | None:
    text = value.strip().upper()
    if text in {"COMPANY", "公司", "公司费用", "固定"}:
        return FinanceScope.COMPANY
    if text in {"PROJECT", "项目", "项目费用"}:
        return FinanceScope.PROJECT
    return None


def map_finance_rows(payload: bytes) -> tuple[list[FinanceImportRow], list[dict[str, object]]]:
    records = read_sheet_rows(payload, header_row=1)
    rows: list[FinanceImportRow] = []
    unresolved: list[dict[str, object]] = []
    for record in records:
        kind = _kind(_first(record, "类型", "kind", "收支"))
        scope = _scope(_first(record, "范围", "scope", "科目范围")) or FinanceScope.PROJECT
        occurred = as_date(_first(record, "日期", "发生日期", "occurred_on"))
        cents = _cents(_first(record, "金额", "金额（元）", "amount"))
        category = _first(record, "类别", "category", "科目") or "未分类"
        if kind is None or occurred is None or cents is None:
            raw = {
                str(key): value
                if isinstance(value, str | int | float | bool) or value is None
                else str(value)
                for key, value in record.items()
                if key != "_row"
            }
            unresolved.append(
                {
                    "row": record.get("_row"),
                    "source_row": record.get("_row"),
                    "source_sheet": "Sheet1",
                    "reason": "ROW_INCOMPLETE",
                    "project_name": _first(record, "项目", "项目名称", "project"),
                    "amount_text": _first(record, "金额", "金额（元）", "amount"),
                    "kind_text": _first(record, "类型", "kind", "收支"),
                    "occurred_text": _first(record, "日期", "发生日期", "occurred_on"),
                    "category": category[:64],
                    "memo": _first(record, "备注", "memo"),
                    "voucher": _first(record, "凭证", "voucher"),
                    "raw": raw,
                }
            )
            continue
        source_row = record.get("_row")
        rows.append(
            FinanceImportRow(
                kind=kind,
                scope=scope,
                project_name=_first(record, "项目", "项目名称", "project"),
                amount_cents=cents,
                occurred_on=occurred
                if isinstance(occurred, date)
                else date.fromisoformat(str(occurred)),
                category=category[:64],
                memo=_first(record, "备注", "memo"),
                voucher=_first(record, "凭证", "voucher"),
                source_row=int(source_row) if source_row else None,
                source_sheet="Sheet1",
            )
        )
    return rows, unresolved
