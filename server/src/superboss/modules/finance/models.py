"""Finance persistence models."""

from datetime import date, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    adjustments: Mapped[list["FinanceAdjustment"]] = relationship(
        back_populates="entry",
        cascade="all, delete-orphan",
        order_by="FinanceAdjustment.created_at",
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
