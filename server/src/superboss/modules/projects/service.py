"""Project application service."""

from __future__ import annotations

import builtins
from datetime import date, timedelta
from itertools import pairwise
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import set_committed_value
from sqlalchemy.sql.base import ExecutableOption

from superboss.core.actors import (
    Actor,
    require_owner,
    require_project_actor,
)
from superboss.core.errors import ConflictError, DomainError, ForbiddenError, NotFoundError
from superboss.core.security import utcnow
from superboss.modules.audit.schemas import AuditEventInput
from superboss.modules.audit.service import AuditService
from superboss.modules.files.models import File, FileState, Folder, FolderVisibility
from superboss.modules.files.service import folder_is_visible, require_folder_read
from superboss.modules.finance.models import FinanceEntry
from superboss.modules.projects.models import (
    NodeStatus,
    Project,
    ProjectLeadHistory,
    ProjectMilestone,
    ProjectNode,
    ProjectScheduleChange,
    ProjectStage,
    ProjectStatus,
    WorkflowTemplate,
    WorkflowTemplateNode,
    WorkflowTemplateVersion,
)
from superboss.modules.projects.schemas import (
    MilestoneReplace,
    ProjectCreate,
    ProjectRead,
    ProjectUpdate,
)
from superboss.modules.projects.workflow import (
    add_days,
    default_stage_dicts,
    plan_from_stages,
    plan_from_start,
    reflow_from_anchor,
)
from superboss.modules.users.models import Role

_IMAGE_TYPES = {
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/webp",
    "image/gif",
}
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def _loaded() -> tuple[ExecutableOption, ...]:
    return (
        selectinload(Project.milestones),
        selectinload(Project.nodes),
        selectinload(Project.schedule_changes),
        selectinload(Project.lead_history),
    )


def _sync_progress(project: Project) -> None:
    nodes = list(project.nodes) if "nodes" in project.__dict__ else []
    counted = [item for item in nodes if item.status is not NodeStatus.SKIPPED]
    if counted:
        done = sum(1 for item in counted if item.status is NodeStatus.DONE)
        project.progress_percent = round(100 * done / len(counted))
        return
    total = len(project.milestones) if "milestones" in project.__dict__ else 0
    if total == 0:
        return
    done = sum(1 for item in project.milestones if item.done_at is not None)
    project.progress_percent = round(100 * done / total)


def _apply_stage(project: Project, stage: ProjectStage) -> None:
    project.stage = stage
    project.status = (
        ProjectStatus.ARCHIVED if stage is ProjectStage.ARCHIVED else ProjectStatus.ACTIVE
    )


def _sorted_nodes(project: Project) -> list[ProjectNode]:
    return sorted(project.nodes, key=lambda item: item.sort_order)


def _has_nodes(project: Project) -> bool:
    return bool("nodes" in project.__dict__ and project.nodes)


def _material_kind(file: File) -> str:
    content = (file.content_type or "").lower()
    name = (file.filename or "").lower()
    if content in _IMAGE_TYPES or any(name.endswith(suffix) for suffix in _IMAGE_SUFFIXES):
        return "photo"
    return "document"


class ProjectService:
    def __init__(self, session: AsyncSession, audit_service: AuditService | None = None) -> None:
        self.session = session
        self.audit_service = audit_service

    async def _record(
        self,
        actor: Actor,
        action: str,
        outcome: str,
        request_id: UUID | None = None,
        project_id: UUID | None = None,
        object_id: UUID | None = None,
    ) -> None:
        if self.audit_service is None or request_id is None:
            return
        await self.audit_service.record(
            AuditEventInput(
                actor=actor,
                action=action,
                object_type="project",
                object_id=object_id if object_id is not None else project_id,
                project_id=project_id,
                outcome=outcome,
                request_id=request_id,
            )
        )

    async def _require_owner(
        self, actor: Actor, action: str, request_id: UUID | None, project_id: UUID | None = None
    ) -> None:
        try:
            require_owner(actor)
        except ForbiddenError:
            await self._record(actor, action, "DENIED", request_id, project_id)
            raise

    async def _get(self, project_id: UUID) -> Project:
        project = cast(
            Project | None,
            await self.session.scalar(
                select(Project).options(*_loaded()).where(Project.id == project_id)
            ),
        )
        if project is None:
            raise NotFoundError("PROJECT_NOT_FOUND", "Project not found")
        return project

    async def _name_taken(self, name: str, *, exclude_id: UUID | None = None) -> bool:
        statement = select(Project.id).where(func.lower(Project.name) == name.lower())
        if exclude_id is not None:
            statement = statement.where(Project.id != exclude_id)
        return (await self.session.scalar(statement)) is not None

    def _attach_nodes(self, project: Project, planned: list[dict[str, Any]]) -> None:
        if "nodes" not in project.__dict__:
            set_committed_value(project, "nodes", [])
        project.nodes.clear()
        for index, item in enumerate(planned):
            project.nodes.append(
                ProjectNode(
                    sort_order=index,
                    title=item["title"],
                    planned_start=item["planned_start"],
                    planned_end=item["planned_end"],
                    preparation=item["preparation"],
                    document_name=item["document_name"],
                    photo_required=item["photo_required"],
                    required_materials=list(item.get("required_materials") or []),
                    duration_days=int(item.get("duration_days") or 1),
                )
            )
        if project.nodes and project.nodes[-1].planned_end:
            project.due_on = project.nodes[-1].planned_end

    async def _ensure_nodes(self, project: Project) -> None:
        """Apply the published default template to a new project that has a start date."""
        if project.starts_on is None:
            return
        if "nodes" not in project.__dict__:
            set_committed_value(project, "nodes", [])
        if project.nodes:
            return
        version = await self._default_template_version()
        if version is not None:
            project.template_version_id = version.id
            stages = [
                {
                    "title": item.title,
                    "duration_days": item.duration_days,
                    "photo_required": item.photo_required,
                    "preparation": list(item.preparation or []),
                    "document_name": item.document_name,
                    "required_materials": list(item.required_materials or []),
                }
                for item in sorted(version.nodes, key=lambda row: row.sort_order)
            ]
            self._attach_nodes(project, plan_from_stages(project.starts_on, stages))
            return
        self._attach_nodes(project, plan_from_start(project.starts_on))

    async def create(
        self, actor: Actor, command: ProjectCreate, request_id: UUID | None = None
    ) -> Project:
        await self._require_owner(actor, "project.create", request_id)
        if await self._name_taken(command.name):
            raise ConflictError("PROJECT_NAME_CONFLICT", "A project with this name already exists")
        project = Project(
            name=command.name,
            description=command.description,
            starts_on=command.starts_on,
            due_on=command.due_on,
            contract_due_on=command.due_on,
            service_fee_cents=command.service_fee_cents,
            lead_user_id=command.lead_user_id,
        )
        _apply_stage(project, command.stage)
        self.session.add(project)
        try:
            await self.session.flush()
        except IntegrityError as error:
            raise ConflictError(
                "PROJECT_NAME_CONFLICT", "A project with this name already exists"
            ) from error
        if command.apply_workflow and command.starts_on is not None:
            await self._ensure_nodes(project)
        await self.session.flush()
        await self.session.refresh(
            project, attribute_names=["milestones", "nodes", "schedule_changes", "lead_history"]
        )
        return project

    async def list(self, actor: Actor, request_id: UUID | None = None) -> list[Project]:
        try:
            require_project_actor(actor)
        except ForbiddenError:
            await self._record(actor, "project.list", "DENIED", request_id)
            raise
        statement = select(Project).options(*_loaded()).order_by(Project.name)
        return list((await self.session.scalars(statement)).all())

    async def due_reminders(self, actor: Actor) -> builtins.list[dict[str, str]]:
        projects = await self.list(actor)
        today = utcnow().date()
        until = today + timedelta(days=3)
        messages: list[dict[str, str]] = []
        for project in projects:
            for node in project.nodes:
                if node.status is not NodeStatus.OPEN or node.planned_end is None:
                    continue
                if not today <= node.planned_end <= until:
                    continue
                days = (node.planned_end - today).days
                when = "今天" if days == 0 else "明天" if days == 1 else f"{days} 天后"
                messages.append(
                    {"message": f"项目《{project.name}》阶段「{node.title}」{when}到期"}
                )
            for milestone in project.milestones:
                if milestone.done_at is not None or milestone.due_on is None:
                    continue
                if not today <= milestone.due_on <= until:
                    continue
                days = (milestone.due_on - today).days
                when = "今天" if days == 0 else "明天" if days == 1 else f"{days} 天后"
                messages.append(
                    {"message": f"项目《{project.name}》里程碑「{milestone.title}」{when}到期"}
                )
        return messages

    async def get(self, actor: Actor, project_id: UUID, request_id: UUID | None = None) -> Project:
        try:
            require_project_actor(actor)
        except ForbiddenError:
            await self._record(actor, "project.read", "DENIED", request_id, project_id)
            raise
        return await self._get(project_id)

    async def update(
        self, actor: Actor, project_id: UUID, command: ProjectUpdate, request_id: UUID | None = None
    ) -> Project:
        await self._require_owner(actor, "project.update", request_id, project_id)
        project = await self._get(project_id)
        values = command.model_dump(exclude_unset=True)
        if not values:
            await self._record(actor, "project.update", "DENIED", request_id, project_id)
            raise DomainError("VALIDATION_ERROR", "Request validation failed", 422)
        if "name" in values and values["name"] is not None:
            if await self._name_taken(values["name"], exclude_id=project.id):
                raise ConflictError(
                    "PROJECT_NAME_CONFLICT", "A project with this name already exists"
                )
            project.name = values["name"]
        if "description" in values and values["description"] is not None:
            project.description = values["description"]
        if "starts_on" in values:
            project.starts_on = values["starts_on"]
        if "contract_due_on" in values:
            project.contract_due_on = values["contract_due_on"]
        if "due_on" in values and not _has_nodes(project):
            project.due_on = values["due_on"]
            if "contract_due_on" not in values and project.contract_due_on is None:
                project.contract_due_on = values["due_on"]
        if "service_fee_cents" in values:
            project.service_fee_cents = values["service_fee_cents"]
        if "lead_user_id" in values:
            previous = project.lead_user_id
            incoming = values["lead_user_id"]
            if previous != incoming:
                self.session.add(
                    ProjectLeadHistory(
                        project_id=project.id,
                        previous_lead_id=previous,
                        new_lead_id=incoming,
                        created_by=actor.subject_id,
                    )
                )
            project.lead_user_id = incoming
        if "stage" in values and values["stage"] is not None:
            _apply_stage(project, values["stage"])
        if (
            "progress_percent" in values
            and values["progress_percent"] is not None
            and not _has_nodes(project)
            and not project.milestones
        ):
            project.progress_percent = values["progress_percent"]
        try:
            await self.session.flush()
        except IntegrityError as error:
            raise ConflictError(
                "PROJECT_NAME_CONFLICT", "A project with this name already exists"
            ) from error
        return project

    async def replace_milestones(
        self,
        actor: Actor,
        project_id: UUID,
        command: MilestoneReplace,
        request_id: UUID | None = None,
    ) -> Project:
        await self._require_owner(actor, "project.milestones.replace", request_id, project_id)
        project = await self._get(project_id)
        now = utcnow()
        project.milestones.clear()
        await self.session.flush()
        for index, item in enumerate(command.milestones):
            project.milestones.append(
                ProjectMilestone(
                    title=item.title,
                    due_on=item.due_on,
                    done_at=now if item.done else None,
                    sort_order=index,
                )
            )
        if not _has_nodes(project):
            if project.milestones:
                _sync_progress(project)
            else:
                project.progress_percent = 0
        await self.session.flush()
        return project

    def _can_edit_nodes(self, actor: Actor, project: Project) -> bool:
        if actor.role == Role.OWNER:
            return True
        return actor.role == Role.STAFF and project.lead_user_id == actor.subject_id

    def _snapshot(self, project: Project) -> builtins.list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for node in _sorted_nodes(project):
            rows.append(
                {
                    "id": str(node.id) if node.id else "",
                    "title": node.title,
                    "planned_start": node.planned_start.isoformat() if node.planned_start else None,
                    "planned_end": node.planned_end.isoformat() if node.planned_end else None,
                    "status": node.status.value,
                }
            )
        return rows

    def _shift_plan(
        self, project: Project, node_id: UUID, days: int
    ) -> tuple[builtins.list[dict[str, object]], builtins.list[str]]:
        target = next((item for item in project.nodes if item.id == node_id), None)
        if target is None:
            raise NotFoundError("NODE_NOT_FOUND", "Project node not found")
        preview: list[dict[str, object]] = []
        proposed: dict[UUID, tuple[date | None, date | None]] = {}
        for node in _sorted_nodes(project):
            locked = node.status is not NodeStatus.OPEN or node.sort_order < target.sort_order
            start = node.planned_start
            end = node.planned_end
            if not locked and start and end:
                start = add_days(start, days)
                end = add_days(end, days)
            proposed[node.id] = (start, end)
            preview.append(
                {
                    "id": str(node.id),
                    "title": node.title,
                    "status": node.status.value,
                    "locked": locked,
                    "old_start": node.planned_start,
                    "old_end": node.planned_end,
                    "new_start": start,
                    "new_end": end,
                }
            )
        violations = self._order_violations(project, proposed)
        return preview, violations

    def _order_violations(
        self, project: Project, proposed: dict[UUID, tuple[date | None, date | None]]
    ) -> builtins.list[str]:
        ordered = [item for item in _sorted_nodes(project) if item.status is not NodeStatus.SKIPPED]
        violations: list[str] = []
        for previous, current in pairwise(ordered):
            _prev_start, prev_end = proposed.get(
                previous.id, (previous.planned_start, previous.planned_end)
            )
            curr_start, _curr_end = proposed.get(
                current.id, (current.planned_start, current.planned_end)
            )
            if prev_end is None or curr_start is None:
                continue
            if curr_start <= prev_end:
                violations.append(
                    f"{current.title} 的开始日 {curr_start.isoformat()} "
                    f"不能早于或等于前序「{previous.title}」结束日 {prev_end.isoformat()}"
                )
        return violations

    def preview_shift(self, project: Project, node_id: UUID, days: int) -> dict[str, object]:
        preview, violations = self._shift_plan(project, node_id, days)
        return {"nodes": preview, "violations": violations, "valid": not violations}

    async def apply_shift(
        self,
        actor: Actor,
        project_id: UUID,
        node_id: UUID,
        days: int,
        reason: str,
    ) -> Project:
        project = await self._get(project_id)
        if not self._can_edit_nodes(actor, project):
            raise ForbiddenError()
        if days == 0:
            raise ConflictError("SHIFT_INVALID", "Shift days cannot be zero")
        cleaned_reason = reason.strip()
        if not cleaned_reason:
            raise DomainError("SHIFT_REASON_REQUIRED", "A schedule change reason is required", 422)
        preview, violations = self._shift_plan(project, node_id, days)
        if violations:
            raise ConflictError("SHIFT_ORDER", violations[0])
        before = self._snapshot(project)
        by_id = {item["id"]: item for item in preview}
        for node in project.nodes:
            row = by_id.get(str(node.id))
            if row is None or row["locked"]:
                continue
            node.planned_start = row["new_start"]  # type: ignore[assignment]
            node.planned_end = row["new_end"]  # type: ignore[assignment]
        last = next((item for item in reversed(_sorted_nodes(project)) if item.planned_end), None)
        if last is not None:
            project.due_on = last.planned_end
        self.session.add(
            ProjectScheduleChange(
                project_id=project.id,
                node_id=node_id,
                days=days,
                reason=cleaned_reason,
                before_json=before,
                after_json=self._snapshot(project),
                created_by=actor.subject_id,
            )
        )
        await self.session.flush()
        await self.session.refresh(project, attribute_names=["nodes", "schedule_changes"])
        return project

    async def _load_files(self, file_ids: builtins.list[UUID]) -> dict[UUID, File]:
        if not file_ids:
            return {}
        rows = (await self.session.scalars(select(File).where(File.id.in_(file_ids)))).all()
        return {item.id: item for item in rows}

    async def serialize_for(self, actor: Actor, project: Project) -> ProjectRead:
        payload = ProjectRead.model_validate(project)
        file_ids: list[UUID] = []
        for node in payload.nodes:
            for item in node.evidence:
                if not isinstance(item, dict):
                    continue
                raw = item.get("file_id")
                if raw is None:
                    continue
                try:
                    file_ids.append(UUID(str(raw)))
                except (TypeError, ValueError):
                    continue
        files = await self._load_files(file_ids)
        folder_ids = {item.folder_id for item in files.values()}
        folders: dict[UUID, Folder] = {}
        if folder_ids:
            folder_rows = (
                await self.session.scalars(select(Folder).where(Folder.id.in_(folder_ids)))
            ).all()
            folders = {item.id: item for item in folder_rows}
        nodes = []
        for node in payload.nodes:
            evidence: list[object] = []
            for item in node.evidence:
                if not isinstance(item, dict):
                    evidence.append(item)
                    continue
                raw = item.get("file_id")
                try:
                    file_id = UUID(str(raw)) if raw is not None else None
                except (TypeError, ValueError):
                    file_id = None
                stored = files.get(file_id) if file_id is not None else None
                folder = folders.get(stored.folder_id) if stored is not None else None
                if folder is None or not folder_is_visible(actor, folder.visibility):
                    evidence.append(
                        {"kind": str(item.get("kind") or "document"), "restricted": True}
                    )
                else:
                    evidence.append(item)
            nodes.append(node.model_copy(update={"evidence": evidence}))
        return payload.model_copy(update={"nodes": tuple(nodes)})

    async def _require_material_file(self, actor: Actor, file: File, project: Project) -> Any:
        folder = None
        getter = getattr(self.session, "get", None)
        if callable(getter):
            folder = await getter(Folder, file.folder_id)
        project_bound = file.project_id is not None and file.project_id == project.id
        if folder is None:
            if not project_bound:
                raise ForbiddenError("FOLDER_FORBIDDEN", "You cannot access this folder")
            return SimpleNamespace(visibility=FolderVisibility.ALL)
        require_folder_read(actor, folder)
        if folder.visibility is not FolderVisibility.ALL and not project_bound:
            raise ForbiddenError("FOLDER_FORBIDDEN", "You cannot access this folder")
        return folder

    async def complete_node(
        self,
        actor: Actor,
        project_id: UUID,
        node_id: UUID,
        evidence: builtins.list[str],
    ) -> Project:
        project = await self._get(project_id)
        if not self._can_edit_nodes(actor, project):
            raise ForbiddenError()
        node = next((item for item in project.nodes if item.id == node_id), None)
        if node is None:
            raise NotFoundError("NODE_NOT_FOUND", "Project node not found")
        if node.status is NodeStatus.DONE:
            raise ConflictError("NODE_DONE", "Node already completed")
        if node.status is NodeStatus.SKIPPED:
            raise ConflictError("NODE_SKIPPED", "Skipped nodes cannot be completed")
        earlier_open = any(
            item.sort_order < node.sort_order and item.status is NodeStatus.OPEN
            for item in project.nodes
        )
        if earlier_open:
            raise ConflictError("NODE_NOT_CURRENT", "Earlier nodes are not completed")
        file_ids: list[UUID] = []
        for raw in evidence:
            text = raw.strip()
            if not text:
                continue
            try:
                file_ids.append(UUID(text))
            except ValueError as error:
                raise ConflictError(
                    "NODE_MATERIALS_INVALID", "Completion materials must be uploaded files"
                ) from error
        required = [str(item) for item in (node.required_materials or [])]
        if node.photo_required and "photo" not in required:
            required.append("photo")
        files = await self._load_files(file_ids)
        stored: list[object] = []
        kinds: set[str] = set()
        for file_id in file_ids:
            file = files.get(file_id)
            if file is None:
                raise ConflictError("NODE_MATERIALS_INVALID", "Completion file was not found")
            if file.state is not FileState.CLEAN:
                raise ConflictError("NODE_MATERIALS_INVALID", "Completion file is not available")
            if file.project_id is not None and file.project_id != project.id:
                raise ConflictError(
                    "NODE_MATERIALS_INVALID", "File does not belong to this project"
                )
            folder = await self._require_material_file(actor, file, project)
            kind = _material_kind(file)
            kinds.add(kind)
            visible_name = file.filename
            if folder.visibility is not FolderVisibility.ALL and file.project_id != project.id:
                visible_name = kind
            stored.append(
                {
                    "file_id": str(file.id),
                    "filename": visible_name,
                    "kind": kind,
                }
            )
        missing = [item for item in required if item not in kinds]
        if required and (not stored or missing):
            raise ConflictError("NODE_MATERIALS_REQUIRED", "Completion materials are required")
        node.evidence = stored
        node.status = NodeStatus.DONE
        node.completed_at = utcnow()
        node.completed_by = actor.subject_id
        _sync_progress(project)
        await self.session.flush()
        await self.session.refresh(project, attribute_names=["nodes", "schedule_changes"])
        return project

    async def skip_node(self, actor: Actor, project_id: UUID, node_id: UUID) -> Project:
        project = await self._get(project_id)
        if not self._can_edit_nodes(actor, project):
            raise ForbiddenError()
        node = next((item for item in project.nodes if item.id == node_id), None)
        if node is None:
            raise NotFoundError("NODE_NOT_FOUND", "Project node not found")
        if node.status is NodeStatus.DONE:
            raise ConflictError("NODE_DONE", "Completed nodes cannot be skipped")
        node.status = NodeStatus.SKIPPED
        _sync_progress(project)
        await self.session.flush()
        return project

    async def apply_workflow(
        self,
        actor: Actor,
        project_id: UUID,
        *,
        starts_on: date | None = None,
        template_version_id: UUID | None = None,
    ) -> Project:
        project = await self._get(project_id)
        if not self._can_edit_nodes(actor, project):
            raise ForbiddenError()
        if _has_nodes(project):
            raise ConflictError("WORKFLOW_EXISTS", "This project already has a workflow")
        start = starts_on or project.starts_on
        if start is None:
            raise DomainError("WORKFLOW_START_REQUIRED", "A start date is required", 422)
        project.starts_on = start
        stages = default_stage_dicts()
        version_id = template_version_id
        if version_id is not None:
            version = await self.session.scalar(
                select(WorkflowTemplateVersion)
                .options(selectinload(WorkflowTemplateVersion.nodes))
                .where(WorkflowTemplateVersion.id == version_id)
            )
            if version is None:
                raise NotFoundError("TEMPLATE_NOT_FOUND", "Workflow template not found")
            stages = [
                {
                    "title": item.title,
                    "duration_days": item.duration_days,
                    "photo_required": item.photo_required,
                    "preparation": list(item.preparation or []),
                    "document_name": item.document_name,
                    "required_materials": list(item.required_materials or []),
                }
                for item in sorted(version.nodes, key=lambda row: row.sort_order)
            ]
            project.template_version_id = version.id
        else:
            default_version = await self._default_template_version()
            if default_version is not None:
                project.template_version_id = default_version.id
                stages = [
                    {
                        "title": item.title,
                        "duration_days": item.duration_days,
                        "photo_required": item.photo_required,
                        "preparation": list(item.preparation or []),
                        "document_name": item.document_name,
                        "required_materials": list(item.required_materials or []),
                    }
                    for item in sorted(default_version.nodes, key=lambda row: row.sort_order)
                ]
        self._attach_nodes(project, plan_from_stages(start, stages))
        _sync_progress(project)
        await self.session.flush()
        await self.session.refresh(project, attribute_names=["nodes", "schedule_changes"])
        return project

    async def reschedule_from_date(
        self,
        actor: Actor,
        project_id: UUID,
        node_id: UUID,
        new_start: date,
        reason: str,
    ) -> Project:
        project = await self._get(project_id)
        if not self._can_edit_nodes(actor, project):
            raise ForbiddenError()
        cleaned_reason = reason.strip()
        if not cleaned_reason:
            raise DomainError("SHIFT_REASON_REQUIRED", "A schedule change reason is required", 422)
        ordered = _sorted_nodes(project)
        target = next((item for item in ordered if item.id == node_id), None)
        if target is None:
            raise NotFoundError("NODE_NOT_FOUND", "Project node not found")
        if target.status is NodeStatus.DONE:
            raise ConflictError("NODE_DONE", "Completed nodes cannot be rescheduled")
        if target.status is NodeStatus.SKIPPED:
            raise ConflictError(
                "NODE_SKIPPED", "Skipped nodes cannot be used as reschedule anchors"
            )
        payload = [
            {
                "id": node.id,
                "duration_days": node.duration_days,
                "planned_start": node.planned_start,
                "planned_end": node.planned_end,
                "status": node.status,
            }
            for node in ordered
        ]
        reflowed = reflow_from_anchor(payload, ordered.index(target), new_start)
        proposed = {
            row["id"]: (row.get("planned_start"), row.get("planned_end"))
            for row in reflowed
            if isinstance(row.get("id"), UUID)
        }
        violations = self._order_violations(project, proposed)
        if violations:
            raise ConflictError("SHIFT_ORDER", violations[0])
        before = self._snapshot(project)
        by_id = {row["id"]: row for row in reflowed}
        for node in project.nodes:
            row = by_id.get(node.id)
            if row is None:
                continue
            node.planned_start = row.get("planned_start")
            node.planned_end = row.get("planned_end")
        last = next((item for item in reversed(_sorted_nodes(project)) if item.planned_end), None)
        if last is not None:
            project.due_on = last.planned_end
        self.session.add(
            ProjectScheduleChange(
                project_id=project.id,
                node_id=node_id,
                days=0,
                reason=cleaned_reason,
                before_json=before,
                after_json=self._snapshot(project),
                created_by=actor.subject_id,
            )
        )
        await self.session.flush()
        await self.session.refresh(project, attribute_names=["nodes", "schedule_changes"])
        return project

    async def complete_service(self, actor: Actor, project_id: UUID) -> Project:
        project = await self._get(project_id)
        if not self._can_edit_nodes(actor, project):
            raise ForbiddenError()
        if project.service_completed_on is not None:
            return project
        open_nodes = [item for item in project.nodes if item.status is NodeStatus.OPEN]
        if open_nodes:
            raise ConflictError("SERVICE_NODES_OPEN", "Open workflow nodes must be finished first")
        project.service_completed_on = utcnow().date()
        project.service_completed_by = actor.subject_id
        await self.session.flush()
        return project

    async def _default_template_version(self) -> WorkflowTemplateVersion | None:
        return cast(
            WorkflowTemplateVersion | None,
            await self.session.scalar(
                select(WorkflowTemplateVersion)
                .join(WorkflowTemplate)
                .options(selectinload(WorkflowTemplateVersion.nodes))
                .where(
                    WorkflowTemplate.is_default.is_(True),
                    WorkflowTemplateVersion.published.is_(True),
                )
                .order_by(WorkflowTemplateVersion.version.desc())
            ),
        )

    async def list_templates(self, actor: Actor) -> builtins.list[dict[str, object]]:
        require_project_actor(actor)
        templates = list(
            (
                await self.session.scalars(
                    select(WorkflowTemplate)
                    .options(
                        selectinload(WorkflowTemplate.versions).selectinload(
                            WorkflowTemplateVersion.nodes
                        )
                    )
                    .order_by(WorkflowTemplate.name)
                )
            ).all()
        )
        payload: list[dict[str, object]] = []
        for template in templates:
            version = next((item for item in template.versions if item.published), None)
            payload.append(
                {
                    "id": str(template.id),
                    "name": template.name,
                    "is_default": template.is_default,
                    "version_id": str(version.id) if version else None,
                    "version": version.version if version else None,
                    "nodes": [
                        {
                            "id": str(node.id),
                            "sort_order": node.sort_order,
                            "title": node.title,
                            "duration_days": node.duration_days,
                            "photo_required": node.photo_required,
                            "document_name": node.document_name,
                            "preparation": node.preparation,
                            "required_materials": node.required_materials,
                        }
                        for node in (version.nodes if version else [])
                    ],
                }
            )
        if payload:
            return payload
        return [
            {
                "id": None,
                "name": "业主大会默认流程",
                "is_default": True,
                "version_id": None,
                "version": 1,
                "nodes": default_stage_dicts(),
            }
        ]

    async def save_default_template(
        self, actor: Actor, name: str, nodes: builtins.list[dict[str, Any]]
    ) -> dict[str, object]:
        require_owner(actor)
        if not nodes:
            raise DomainError("TEMPLATE_EMPTY", "A template needs at least one node", 422)
        template = await self.session.scalar(
            select(WorkflowTemplate).where(WorkflowTemplate.is_default.is_(True))
        )
        if template is None:
            template = WorkflowTemplate(name=name or "业主大会默认流程", is_default=True)
            self.session.add(template)
            await self.session.flush()
        elif name.strip():
            template.name = name.strip()[:255]
        latest = await self.session.scalar(
            select(func.max(WorkflowTemplateVersion.version)).where(
                WorkflowTemplateVersion.template_id == template.id
            )
        )
        version = WorkflowTemplateVersion(
            template_id=template.id,
            version=int(latest or 0) + 1,
            published=True,
        )
        self.session.add(version)
        await self.session.flush()
        for index, item in enumerate(nodes):
            title = str(item.get("title") or "").strip()
            if not title:
                raise DomainError("TEMPLATE_NODE_TITLE", "Each node needs a title", 422)
            self.session.add(
                WorkflowTemplateNode(
                    version_id=version.id,
                    sort_order=index,
                    title=title[:255],
                    duration_days=max(int(item.get("duration_days") or 1), 1),
                    photo_required=bool(item.get("photo_required")),
                    document_name=str(item.get("document_name") or "")[:255],
                    preparation=list(item.get("preparation") or []),
                    required_materials=list(item.get("required_materials") or []),
                )
            )
        await self.session.flush()
        listed = await self.list_templates(actor)
        return next(item for item in listed if item.get("is_default"))

    async def delete(self, actor: Actor, project_id: UUID, request_id: UUID | None = None) -> None:
        await self._require_owner(actor, "project.delete", request_id, project_id)
        project = await self._get(project_id)
        linked = await self.session.scalar(
            select(FinanceEntry.id).where(FinanceEntry.project_id == project.id).limit(1)
        )
        if linked is not None:
            raise ConflictError("PROJECT_HAS_ENTRIES", "Project has finance entries")
        try:
            await self.session.delete(project)
            await self.session.flush()
        except IntegrityError as error:
            raise ConflictError("PROJECT_HAS_ENTRIES", "Project has finance entries") from error

    async def commit_and_record_success(
        self,
        actor: Actor,
        action: str,
        request_id: UUID,
        project_id: UUID | None = None,
        *,
        object_id: UUID | None = None,
    ) -> None:
        """Close the business transaction before independently committing success evidence."""
        await self.session.commit()
        await self._record(actor, action, "SUCCESS", request_id, project_id, object_id)
