"""Append-only audit event recording."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from superboss.modules.audit.models import AuditLog
from superboss.modules.audit.schemas import AuditEventInput

_AUDIT_CONFLICT = "audit event key conflicts with immutable event"


def _same_event(
    existing: AuditLog,
    *,
    actor_kind: str,
    actor_id: UUID | None,
    action: str,
    object_type: str,
    object_id: UUID | None,
    project_id: UUID | None,
    outcome: str,
    request_id: UUID | None,
    metadata: dict[str, object],
) -> bool:
    return (
        existing.actor_kind == actor_kind
        and existing.actor_id == actor_id
        and existing.action == action
        and existing.object_type == object_type
        and existing.object_id == object_id
        and existing.project_id == project_id
        and existing.outcome == outcome
        and existing.request_id == request_id
        and existing.metadata_json == metadata
    )


async def write_audit(
    session: AsyncSession,
    *,
    actor_kind: str,
    actor_id: UUID | None,
    action: str,
    object_type: str,
    object_id: UUID | None = None,
    project_id: UUID | None = None,
    outcome: str,
    request_id: UUID | None = None,
    metadata: dict[str, object] | None = None,
    event_key: UUID | None = None,
) -> UUID:
    """Insert an audit row, or reuse an identical event_key already in this session."""
    payload = metadata if metadata is not None else {}
    if event_key is not None:
        existing = await session.scalar(select(AuditLog).where(AuditLog.event_key == event_key))
        if existing is not None:
            if not _same_event(
                existing,
                actor_kind=actor_kind,
                actor_id=actor_id,
                action=action,
                object_type=object_type,
                object_id=object_id,
                project_id=project_id,
                outcome=outcome,
                request_id=request_id,
                metadata=payload,
            ):
                raise RuntimeError(_AUDIT_CONFLICT)
            return existing.id
    row = AuditLog(
        actor_kind=actor_kind,
        actor_id=actor_id,
        action=action,
        object_type=object_type,
        object_id=object_id,
        project_id=project_id,
        outcome=outcome,
        metadata_json=payload,
        request_id=request_id,
        event_key=event_key,
    )
    session.add(row)
    await session.flush()
    return row.id


class AuditService:
    """Persist audit evidence in its own short transaction."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def record(self, event: AuditEventInput) -> UUID:
        expected_metadata = {
            **event.metadata,
            "actor_role": event.actor.role.value if event.actor.role is not None else None,
        }

        async def persist(session: AsyncSession) -> UUID:
            return await write_audit(
                session,
                actor_kind="user" if event.actor.role is not None else "system",
                actor_id=event.actor.subject_id,
                action=event.action,
                object_type=event.object_type,
                object_id=event.object_id,
                project_id=event.project_id,
                outcome=event.outcome,
                request_id=event.request_id,
                metadata=expected_metadata,
                event_key=event.event_key,
            )

        async with self.session_factory() as session:
            try:
                audit_id = await persist(session)
                await session.commit()
            except IntegrityError:
                await session.rollback()
                if event.event_key is None:
                    raise
                return await persist(session)
            return audit_id

    async def list_events(self, *, limit: int = 100, action: str | None = None) -> list[AuditLog]:
        bound = min(max(limit, 1), 200)
        statement = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(bound)
        if action:
            statement = statement.where(AuditLog.action == action)
        async with self.session_factory() as session:
            return list((await session.scalars(statement)).all())
