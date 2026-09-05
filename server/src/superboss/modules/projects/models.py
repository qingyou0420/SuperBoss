"""Project persistence models."""

from datetime import date, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from superboss.core.db import Base


class ProjectStatus(StrEnum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class ProjectStage(StrEnum):
    PLANNING = "PLANNING"
    ACTIVE = "ACTIVE"
    DELIVERING = "DELIVERING"
    REVIEW = "REVIEW"
    ARCHIVED = "ARCHIVED"


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (
        CheckConstraint("status IN ('ACTIVE', 'ARCHIVED')", name="ck_projects_status"),
        CheckConstraint(
            "stage IN ('PLANNING','ACTIVE','DELIVERING','REVIEW','ARCHIVED')",
            name="ck_projects_stage",
        ),
        CheckConstraint(
            "progress_percent BETWEEN 0 AND 100",
            name="ck_projects_progress",
        ),
        CheckConstraint(
            "name = btrim(name, E' \\t\\r\\n' || chr(160))", name="ck_projects_name_trimmed"
        ),
        CheckConstraint("char_length(name) BETWEEN 1 AND 255", name="ck_projects_name_length"),
        Index("uq_projects_name_ci", text("lower(name)"), unique=True),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    is_test: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[ProjectStatus] = mapped_column(
        Enum(ProjectStatus, name="project_status", native_enum=False),
        default=ProjectStatus.ACTIVE,
        nullable=False,
    )
    stage: Mapped[ProjectStage] = mapped_column(
        Enum(ProjectStage, name="project_stage", native_enum=False),
        default=ProjectStage.PLANNING,
        server_default="PLANNING",
        nullable=False,
    )
    progress_percent: Mapped[int] = mapped_column(
        SmallInteger, default=0, server_default="0", nullable=False
    )
    starts_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    due_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    milestones: Mapped[list["ProjectMilestone"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="ProjectMilestone.sort_order",
    )


class ProjectMilestone(Base):
    __tablename__ = "project_milestones"
    __table_args__ = (
        CheckConstraint(
            "title = btrim(title, E' \\t\\r\\n' || chr(160))",
            name="ck_project_milestones_title_trimmed",
        ),
        CheckConstraint(
            "char_length(title) BETWEEN 1 AND 255",
            name="ck_project_milestones_title_length",
        ),
        Index("ix_project_milestones_project_sort", "project_id", "sort_order"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    due_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    project: Mapped[Project] = relationship(back_populates="milestones")


class ProjectMember(Base):
    __tablename__ = "project_members"

    project_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
