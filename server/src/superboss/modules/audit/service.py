"""Append-only audit event recording."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from superboss.modules.audit.models import AuditLog
from superboss.modules.audit.schemas import AuditEventInput


class AuditService:
    """Persist audit evidence in its own short transaction."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def record(self, event: AuditEventInput) -> UUID:
        async with self.session_factory() as session:
            expected_metadata = {
                **event.metadata,
                "actor_role": event.actor.role.value if event.actor.role is not None else None,
            }
            if event.event_key is not None:
                existing = await session.scalar(
                    select(AuditLog).where(AuditLog.event_key == event.event_key)
                )
                if existing is not None:
                    return self._matching_id(existing, event, expected_metadata)
            audit_log = AuditLog(
                actor_kind=event.actor.kind,
                actor_id=event.actor.subject_id,
                action=event.action,
                object_type=event.object_type,
                object_id=event.object_id,
                project_id=event.project_id,
                outcome=event.outcome,
                metadata_json=expected_metadata,
                request_id=event.request_id,
                event_key=event.event_key,
            )
            session.add(audit_log)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                if event.event_key is None:
                    raise
                existing = await session.scalar(
                    select(AuditLog).where(AuditLog.event_key == event.event_key)
                )
                if existing is None:
                    raise
                return self._matching_id(existing, event, expected_metadata)
            return audit_log.id

    @staticmethod
    def _matching_id(
        existing: AuditLog, event: AuditEventInput, metadata: dict[str, object]
    ) -> UUID:
        if (
            existing.actor_kind == event.actor.kind
            and existing.actor_id == event.actor.subject_id
            and existing.action == event.action
            and existing.object_type == event.object_type
            and existing.object_id == event.object_id
            and existing.project_id == event.project_id
            and existing.outcome == event.outcome
            and existing.request_id == event.request_id
            and existing.metadata_json == metadata
        ):
            return existing.id
        raise RuntimeError("audit event key conflicts with immutable event")
