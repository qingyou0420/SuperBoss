"""Validate card payloads and commit them through domain services."""

import logging
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from superboss.core.actors import Actor
from superboss.core.errors import DomainError, NotFoundError
from superboss.core.security import utcnow
from superboss.modules.agent.models import (
    AgentCard,
    AgentMemory,
    CardKind,
    CardStatus,
    MemoryStatus,
)
from superboss.modules.agent.schemas import (
    CARD_MODELS,
    FileMoveCard,
    FinanceAdjustCard,
    FinanceEntryCard,
    MemoryCard,
    MilestoneChangeCard,
    ProjectCreateCard,
    ProjectUpdateCard,
)
from superboss.modules.audit.schemas import AuditEventInput
from superboss.modules.audit.service import AuditService
from superboss.modules.files.schemas import FilePatch
from superboss.modules.files.service import FileService
from superboss.modules.files.storage import ObjectStorage
from superboss.modules.finance.models import CreatedVia
from superboss.modules.finance.schemas import FinanceAdjustmentCreate, FinanceEntryCreate
from superboss.modules.finance.service import FinanceService
from superboss.modules.knowledge.schemas import KnowledgeIngestCard
from superboss.modules.knowledge.service import KnowledgeService
from superboss.modules.projects.schemas import (
    MilestoneReplace,
    MilestoneWrite,
    ProjectCreate,
    ProjectUpdate,
)
from superboss.modules.projects.service import ProjectService

_LOG = logging.getLogger(__name__)


def parse_card_payload(kind: CardKind, payload: object) -> object:
    model = CARD_MODELS[kind]
    return model.model_validate(payload)


async def commit_card(
    session: AsyncSession,
    actor: Actor,
    card: AgentCard,
    *,
    request_id: UUID,
    storage: ObjectStorage | None,
    audit: AuditService | None,
) -> AgentCard:
    try:
        parsed = parse_card_payload(card.kind, card.payload)
        object_type, object_id = await _dispatch(session, actor, card, parsed, request_id, storage)
    except DomainError as error:
        card.status = CardStatus.FAILED
        card.error = error.code
        card.decided_at = utcnow()
        raise
    except Exception as error:
        _LOG.exception("card commit failed for %s", card.id)
        card.status = CardStatus.FAILED
        card.error = "CARD_COMMIT_FAILED"
        card.decided_at = utcnow()
        raise DomainError("CARD_COMMIT_FAILED", "Card could not be committed", 409) from error
    card.status = CardStatus.COMMITTED
    card.decided_at = utcnow()
    card.committed_object_type = object_type
    card.committed_object_id = object_id
    card.error = None
    if audit is not None:
        await audit.record(
            AuditEventInput(
                actor=actor,
                action="agent.card.confirm",
                object_type="agent_card",
                object_id=card.id,
                project_id=None,
                outcome="SUCCESS",
                request_id=request_id,
                metadata={"kind": card.kind.value, "committed_object_type": object_type},
            )
        )
    return card


async def _dispatch(
    session: AsyncSession,
    actor: Actor,
    card: AgentCard,
    parsed: object,
    request_id: UUID,
    storage: ObjectStorage | None,
) -> tuple[str, UUID]:
    if isinstance(parsed, FinanceEntryCard):
        created = await FinanceService(session, None).create_entry(
            actor,
            FinanceEntryCreate.model_validate(parsed.model_dump()),
            request_id,
            created_via=CreatedVia.CARD,
            card_id=card.id,
        )
        return "finance_entry", created.id
    if isinstance(parsed, FinanceAdjustCard):
        updated = await FinanceService(session, None).adjust_entry(
            actor,
            parsed.entry_id,
            FinanceAdjustmentCreate(
                field=parsed.field, new_value=parsed.new_value, reason=parsed.reason
            ),
            request_id,
        )
        return "finance_entry", updated.id
    if isinstance(parsed, ProjectCreateCard):
        project = await ProjectService(session).create(
            actor,
            ProjectCreate(
                name=parsed.name,
                description=parsed.description,
                stage=parsed.stage,
            ),
            request_id,
        )
        if parsed.milestones:
            project = await ProjectService(session).replace_milestones(
                actor, project.id, MilestoneReplace(milestones=parsed.milestones), request_id
            )
        return "project", project.id
    if isinstance(parsed, ProjectUpdateCard):
        project = await ProjectService(session).update(
            actor,
            parsed.project_id,
            ProjectUpdate.model_validate(
                parsed.model_dump(exclude={"project_id"}, exclude_none=True)
            ),
            request_id,
        )
        return "project", project.id
    if isinstance(parsed, MilestoneChangeCard):
        service = ProjectService(session)
        current = await service.get(actor, parsed.project_id, request_id)
        remaining = [item for item in current.milestones if item.id not in set(parsed.remove)]
        by_id = {item.id: item for item in remaining}
        for change in parsed.update:
            target = by_id.get(change.id)
            if target is None:
                raise NotFoundError("MILESTONE_NOT_FOUND", "Milestone not found")
            if change.title is not None:
                target.title = change.title
            if change.due_on is not None:
                target.due_on = change.due_on
            if change.done is True and target.done_at is None:
                target.done_at = utcnow()
            if change.done is False:
                target.done_at = None
        writes = [
            MilestoneWrite(
                title=item.title,
                due_on=item.due_on,
                done=item.done_at is not None,
                sort_order=item.sort_order,
            )
            for item in remaining
        ]
        next_order = max((item.sort_order for item in remaining), default=-1) + 1
        for index, added in enumerate(parsed.add):
            writes.append(
                MilestoneWrite(
                    title=added.title,
                    due_on=added.due_on,
                    done=added.done,
                    sort_order=added.sort_order or next_order + index,
                )
            )
        project = await service.replace_milestones(
            actor, parsed.project_id, MilestoneReplace(milestones=writes), request_id
        )
        return "project", project.id
    if isinstance(parsed, FileMoveCard):
        patched = await FileService(session, storage).patch_file(
            actor,
            parsed.file_id,
            FilePatch(folder_id=parsed.target_folder_id, filename=parsed.new_name),
        )
        return "file", patched.id
    if isinstance(parsed, KnowledgeIngestCard):
        doc = await KnowledgeService(session).ingest(actor, parsed)
        return "knowledge_doc", doc.id
    if isinstance(parsed, MemoryCard):
        memory = AgentMemory(
            kind=parsed.kind,
            content=parsed.content,
            importance=parsed.importance,
            pinned=parsed.pinned,
            status=MemoryStatus.ACTIVE,
            source_message_id=card.message_id,
        )
        session.add(memory)
        await session.flush()
        return "memory", memory.id
    raise DomainError("CARD_KIND_UNSUPPORTED", "Unsupported card kind", 422)
