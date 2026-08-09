"""Append-only audit event recording."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from superboss.modules.audit.models import AuditLog
from superboss.modules.audit.schemas import AuditEventInput


class AuditService:
    """Persist audit evidence in its own short transaction."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def record(self, event: AuditEventInput) -> UUID:
        async with self.session_factory() as session:
            audit_log = AuditLog(
                actor_kind=event.actor.kind,
                actor_id=event.actor.subject_id,
                action=event.action,
                object_type=event.object_type,
                object_id=event.object_id,
                project_id=event.project_id,
                outcome=event.outcome,
                metadata_json={
                    **event.metadata,
                    "actor_role": event.actor.role.value if event.actor.role is not None else None,
                },
                request_id=event.request_id,
            )
            session.add(audit_log)
            await session.commit()
            return audit_log.id
