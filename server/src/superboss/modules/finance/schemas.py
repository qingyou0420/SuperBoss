"""Finance HTTP schemas."""

import re
from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from superboss.modules.finance.models import (
    CreatedVia,
    FinanceKind,
    FinanceScope,
    FinanceVisibility,
)

_EDGE = " \t\r\n\u00a0"
_MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
AdjustableField = Literal["amount_cents", "occurred_on", "category", "memo", "visibility"]


def _canonical_text(value: str, *, maximum: int, minimum: int = 1) -> str:
    normalized = value.strip(_EDGE)
    if not minimum <= len(normalized) <= maximum:
        raise ValueError("text must contain the allowed number of characters")
    return normalized


class FinanceEntryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: FinanceKind
    scope: FinanceScope
    project_id: UUID | None = None
    amount_cents: int = Field(ge=1, le=1_000_000_000_000)
    occurred_on: date
    category: str
    memo: str = ""
    visibility: FinanceVisibility | None = None

    @field_validator("category")
    @classmethod
    def canonical_category(cls, value: str) -> str:
        return _canonical_text(value, maximum=64)

    @field_validator("memo")
    @classmethod
    def canonical_memo(cls, value: str) -> str:
        return value.strip(_EDGE)[:1000]

    @model_validator(mode="after")
    def scope_matches_project(self) -> "FinanceEntryCreate":
        if self.scope is FinanceScope.PROJECT and self.project_id is None:
            raise ValueError("project_id is required for project-scoped entries")
        if self.scope is FinanceScope.COMPANY and self.project_id is not None:
            raise ValueError("company-scoped entries cannot reference a project")
        return self


class FinanceAdjustmentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: AdjustableField
    new_value: str
    reason: str

    @field_validator("reason")
    @classmethod
    def canonical_reason(cls, value: str) -> str:
        return _canonical_text(value, maximum=500)

    @field_validator("new_value")
    @classmethod
    def strip_new_value(cls, value: str) -> str:
        return value.strip(_EDGE)

    @model_validator(mode="after")
    def canonical_new_value(self) -> "FinanceAdjustmentCreate":
        if self.field == "amount_cents":
            if not self.new_value.isdigit():
                raise ValueError("amount_cents must be a positive integer")
            amount = int(self.new_value)
            if not 1 <= amount <= 1_000_000_000_000:
                raise ValueError("amount_cents is out of range")
            self.new_value = str(amount)
            return self
        if self.field == "occurred_on":
            self.new_value = date.fromisoformat(self.new_value).isoformat()
            return self
        if self.field == "visibility":
            self.new_value = FinanceVisibility(self.new_value).value
            return self
        if self.field == "category":
            self.new_value = _canonical_text(self.new_value, maximum=64)
            return self
        self.new_value = self.new_value[:1000]
        return self


class FinanceAdjustmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    field: str
    old_value: str
    new_value: str
    reason: str
    created_at: datetime


class FinancePaymentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    paid_on: date
    amount_cents: int
    created_at: datetime


class FinanceEntryRead(BaseModel):
    id: UUID
    kind: FinanceKind
    scope: FinanceScope
    project_id: UUID | None
    project_name: str | None
    amount_cents: int
    currency: str
    occurred_on: date
    category: str
    memo: str
    visibility: FinanceVisibility
    created_via: CreatedVia
    created_at: datetime
    adjustments: list[FinanceAdjustmentRead]
    batch_key: str = ""
    paid_on: date | None = None
    paid_cents: int | None = None
    voucher: str = ""
    paid_total_cents: int = 0
    unpaid_cents: int = 0
    payments: list[FinancePaymentRead] = Field(default_factory=list)


class CompanyTotals(BaseModel):
    cost_cents: int
    income_cents: int


class ProjectTotals(BaseModel):
    project_id: UUID
    project_name: str
    cost_cents: int
    income_cents: int | None = None


class FinanceSummary(BaseModel):
    month: str
    company: CompanyTotals | None
    projects: list[ProjectTotals]

    @field_validator("month")
    @classmethod
    def canonical_month(cls, value: str) -> str:
        if not _MONTH.fullmatch(value):
            raise ValueError("month must be YYYY-MM")
        return value


class FinanceImportRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: FinanceKind
    scope: FinanceScope
    project_id: UUID | None = None
    project_name: str = ""
    amount_cents: int = Field(ge=1, le=1_000_000_000_000)
    occurred_on: date
    category: str
    memo: str = ""
    voucher: str = ""
    source_row: int | None = Field(default=None, ge=1)
    source_sheet: str = ""

    @field_validator("category")
    @classmethod
    def canonical_category(cls, value: str) -> str:
        return _canonical_text(value, maximum=64)

    @field_validator("memo", "voucher", "project_name")
    @classmethod
    def canonical_optional(cls, value: str) -> str:
        return value.strip(_EDGE)[:1000]


class FinanceImportCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch_key: str
    rows: list[FinanceImportRow] = Field(default_factory=list, max_length=500)

    @field_validator("batch_key")
    @classmethod
    def canonical_batch(cls, value: str) -> str:
        return _canonical_text(value, maximum=64)


class FinanceImportRowRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    batch_key: str
    row_index: int
    status: str
    reason: str
    fingerprint: str
    entry_id: UUID | None = None
    payload: dict[str, object] = Field(default_factory=dict)


class FinanceImportRowListRead(BaseModel):
    items: list[FinanceImportRowRead]
    total: int


class FinanceImportResolve(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["link_voucher", "insert_independent"]
    entry_id: UUID | None = None


class FinancePayCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paid_on: date
    paid_cents: int | None = Field(default=None, ge=1, le=1_000_000_000_000)
    idempotency_key: str = ""

    @field_validator("idempotency_key")
    @classmethod
    def strip_key(cls, value: str) -> str:
        return value.strip()[:64]


class CompanyMonthCostWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount_cents: int = Field(ge=0, le=1_000_000_000_000)


class CompanyMonthCostRead(BaseModel):
    month: str
    amount_cents: int
