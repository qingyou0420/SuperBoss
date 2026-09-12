"""Project persistence models."""

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
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
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
    service_fee_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    contract_due_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    service_completed_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    service_completed_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    template_version_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    lead_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    lead_history: Mapped[list["ProjectLeadHistory"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="ProjectLeadHistory.created_at.desc()",
    )
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
    nodes: Mapped[list["ProjectNode"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="ProjectNode.sort_order",
    )
    schedule_changes: Mapped[list["ProjectScheduleChange"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="ProjectScheduleChange.created_at.desc()",
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


class NodeStatus(StrEnum):
    OPEN = "OPEN"
    DONE = "DONE"
    SKIPPED = "SKIPPED"


class ProjectNode(Base):
    __tablename__ = "project_nodes"
    __table_args__ = (
        CheckConstraint("status IN ('OPEN','DONE','SKIPPED')", name="ck_project_nodes_status"),
        CheckConstraint("char_length(title) BETWEEN 1 AND 255", name="ck_project_nodes_title"),
        Index("ix_project_nodes_project", "project_id", "sort_order"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    planned_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    planned_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[NodeStatus] = mapped_column(
        Enum(
            NodeStatus,
            name="project_node_status",
            native_enum=False,
            values_callable=lambda items: [item.value for item in items],
        ),
        default=NodeStatus.OPEN,
        nullable=False,
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    preparation: Mapped[list[object]] = mapped_column(JSONB, default=list, nullable=False)
    document_name: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    photo_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    required_materials: Mapped[list[object]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb"), nullable=False
    )
    duration_days: Mapped[int] = mapped_column(
        Integer, default=1, server_default=text("1"), nullable=False
    )
    evidence: Mapped[list[object]] = mapped_column(JSONB, default=list, nullable=False)
    project: Mapped[Project] = relationship(back_populates="nodes")


class ProjectLeadHistory(Base):
    __tablename__ = "project_lead_history"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    previous_lead_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    new_lead_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_by: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    project: Mapped[Project] = relationship(back_populates="lead_history")


class WorkflowTemplate(Base):
    __tablename__ = "workflow_templates"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_default: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    versions: Mapped[list["WorkflowTemplateVersion"]] = relationship(
        back_populates="template",
        cascade="all, delete-orphan",
        order_by="WorkflowTemplateVersion.version.desc()",
    )


class WorkflowTemplateVersion(Base):
    __tablename__ = "workflow_template_versions"
    __table_args__ = (
        UniqueConstraint("template_id", "version", name="uq_workflow_template_versions"),
        Index("ix_workflow_template_versions_template", "template_id", "version"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    template_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("workflow_templates.id", ondelete="CASCADE"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    published: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true(), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    template: Mapped[WorkflowTemplate] = relationship(back_populates="versions")
    nodes: Mapped[list["WorkflowTemplateNode"]] = relationship(
        back_populates="version_row",
        cascade="all, delete-orphan",
        order_by="WorkflowTemplateNode.sort_order",
    )


class WorkflowTemplateNode(Base):
    __tablename__ = "workflow_template_nodes"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    version_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("workflow_template_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    duration_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    photo_required: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )
    document_name: Mapped[str] = mapped_column(
        String(255), default="", server_default=text("''"), nullable=False
    )
    preparation: Mapped[list[object]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb"), nullable=False
    )
    required_materials: Mapped[list[object]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb"), nullable=False
    )
    version_row: Mapped[WorkflowTemplateVersion] = relationship(back_populates="nodes")


class ProjectScheduleChange(Base):
    __tablename__ = "project_schedule_changes"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    node_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("project_nodes.id", ondelete="SET NULL"), nullable=True
    )
    days: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    before_json: Mapped[list[object]] = mapped_column(JSONB, default=list, nullable=False)
    after_json: Mapped[list[object]] = mapped_column(JSONB, default=list, nullable=False)
    created_by: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    project: Mapped[Project] = relationship(back_populates="schedule_changes")
