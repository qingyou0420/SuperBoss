"""Finance persistence models."""

from datetime import date, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from superboss.core.db import Base


class FinanceKind(StrEnum):
    COST = "COST"
    INCOME = "INCOME"


class FinanceScope(StrEnum):
    COMPANY = "COMPANY"
    PROJECT = "PROJECT"


class FinanceVisibility(StrEnum):
    ALL = "ALL"
    MANAGEMENT = "MANAGEMENT"
    OWNER_ONLY = "OWNER_ONLY"


class CreatedVia(StrEnum):
    FORM = "FORM"
    CARD = "CARD"


class FinanceEntry(Base):
    __tablename__ = "finance_entries"
    __table_args__ = (
        CheckConstraint("kind IN ('COST','INCOME')", name="ck_finance_entries_kind"),
        CheckConstraint("scope IN ('COMPANY','PROJECT')", name="ck_finance_entries_scope"),
        CheckConstraint(
            "visibility IN ('ALL','MANAGEMENT','OWNER_ONLY')",
            name="ck_finance_entries_visibility",
        ),
        CheckConstraint("created_via IN ('FORM','CARD')", name="ck_finance_entries_created_via"),
        CheckConstraint("currency = 'CNY'", name="ck_finance_entries_currency"),
        CheckConstraint(
            "amount_cents BETWEEN 1 AND 1000000000000",
            name="ck_finance_entries_amount",
        ),
        CheckConstraint(
            "(scope = 'PROJECT' AND project_id IS NOT NULL) OR "
            "(scope = 'COMPANY' AND project_id IS NULL)",
            name="ck_finance_entries_project_scope",
        ),
        CheckConstraint(
            "category = btrim(category, E' \\t\\r\\n' || chr(160))",
            name="ck_finance_entries_category_trimmed",
        ),
        CheckConstraint(
            "char_length(category) BETWEEN 1 AND 64",
            name="ck_finance_entries_category_length",
        ),
        Index("ix_finance_entries_occurred_on", "occurred_on"),
        Index("ix_finance_entries_project", "project_id"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    kind: Mapped[FinanceKind] = mapped_column(
        Enum(FinanceKind, name="finance_kind", native_enum=False), nullable=False
    )
    scope: Mapped[FinanceScope] = mapped_column(
        Enum(FinanceScope, name="finance_scope", native_enum=False), nullable=False
    )
    project_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("projects.id", ondelete="RESTRICT"), nullable=True
    )
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="CNY", nullable=False)
    occurred_on: Mapped[date] = mapped_column(Date, nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    memo: Mapped[str] = mapped_column(Text, default="", nullable=False)
    visibility: Mapped[FinanceVisibility] = mapped_column(
        Enum(FinanceVisibility, name="finance_visibility", native_enum=False),
        nullable=False,
    )
    created_by: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_via: Mapped[CreatedVia] = mapped_column(
        Enum(CreatedVia, name="finance_created_via", native_enum=False),
        default=CreatedVia.FORM,
        nullable=False,
    )
    card_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    batch_key: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    paid_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    paid_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    voucher: Mapped[str] = mapped_column(
        String(255), default="", server_default=text("''"), nullable=False
    )
    voucher_file_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("files.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    adjustments: Mapped[list["FinanceAdjustment"]] = relationship(
        back_populates="entry",
        cascade="all, delete-orphan",
        order_by="FinanceAdjustment.created_at",
    )
    payments: Mapped[list["FinancePayment"]] = relationship(
        back_populates="entry",
        cascade="all, delete-orphan",
        order_by="FinancePayment.paid_on",
    )


class FinanceAdjustment(Base):
    __tablename__ = "finance_adjustments"
    __table_args__ = (
        CheckConstraint(
            "field IN ('amount_cents','occurred_on','category','memo','visibility')",
            name="ck_finance_adjustments_field",
        ),
        CheckConstraint(
            "char_length(reason) BETWEEN 1 AND 500", name="ck_finance_adjustments_reason"
        ),
        Index("ix_finance_adjustments_entry", "entry_id"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    entry_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("finance_entries.id", ondelete="CASCADE"),
        nullable=False,
    )
    field: Mapped[str] = mapped_column(String(32), nullable=False)
    old_value: Mapped[str] = mapped_column(Text, nullable=False)
    new_value: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    created_by: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    entry: Mapped[FinanceEntry] = relationship(back_populates="adjustments")


class FinancePayment(Base):
    __tablename__ = "finance_payments"
    __table_args__ = (
        CheckConstraint(
            "amount_cents BETWEEN 1 AND 1000000000000",
            name="ck_finance_payments_amount",
        ),
        Index("ix_finance_payments_entry", "entry_id", "paid_on"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    entry_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("finance_entries.id", ondelete="CASCADE"),
        nullable=False,
    )
    paid_on: Mapped[date] = mapped_column(Date, nullable=False)
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(
        String(64), default="", server_default=text("''"), nullable=False
    )
    created_by: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    entry: Mapped[FinanceEntry] = relationship(back_populates="payments")


class FinanceImportBatch(Base):
    __tablename__ = "finance_import_batches"

    batch_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_by: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    source_filename: Mapped[str] = mapped_column(
        String(255), default="", server_default=text("''"), nullable=False
    )
    rows: Mapped[list["FinanceImportRow"]] = relationship(
        back_populates="batch", cascade="all, delete-orphan"
    )


class FinanceImportRow(Base):
    __tablename__ = "finance_import_rows"
    __table_args__ = (
        CheckConstraint(
            "status IN ('INSERTED','SKIPPED','DUPLICATE','UNRESOLVED','LINKED')",
            name="ck_finance_import_rows_status",
        ),
        UniqueConstraint("batch_key", "row_index", name="uq_finance_import_rows_batch_row"),
        Index("ix_finance_import_rows_batch", "batch_key", "row_index"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    batch_key: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("finance_import_batches.batch_key", ondelete="CASCADE"),
        nullable=False,
    )
    row_index: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str] = mapped_column(
        String(64), default="", server_default=text("''"), nullable=False
    )
    fingerprint: Mapped[str] = mapped_column(
        String(512), default="", server_default=text("''"), nullable=False
    )
    entry_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("finance_entries.id", ondelete="SET NULL"), nullable=True
    )
    payload: Mapped[dict[str, object]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )
    batch: Mapped[FinanceImportBatch] = relationship(back_populates="rows")


class CompanyMonthCost(Base):
    __tablename__ = "company_month_costs"
    __table_args__ = (CheckConstraint("amount_cents >= 0", name="ck_company_month_costs_amount"),)

    month: Mapped[str] = mapped_column(String(7), primary_key=True)
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)


class CompanySetting(Base):
    __tablename__ = "company_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value_json: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, nullable=False)
    is_placeholder: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class RewardAllocation(Base):
    __tablename__ = "reward_allocations"
    __table_args__ = (
        CheckConstraint("kind IN ('SURPLUS','POOL')", name="ck_reward_allocations_kind"),
        CheckConstraint("share_cents >= 0", name="ck_reward_allocations_share"),
        Index("ix_reward_allocations_project", "project_id", "kind"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    person_name: Mapped[str] = mapped_column(String(64), nullable=False)
    share_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    is_placeholder: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    note: Mapped[str] = mapped_column(Text, default="", nullable=False)
