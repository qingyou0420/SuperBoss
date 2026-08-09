"""Audit service persistence and redaction tests."""

from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from superboss.core.actors import Actor
from superboss.modules.audit.models import AuditLog
from superboss.modules.audit.schemas import AuditEventInput
from superboss.modules.audit.service import AuditService
from superboss.modules.projects.models import Project
from superboss.modules.users.models import Role


@pytest.mark.asyncio
async def test_record_redacts_forbidden_metadata_keys_at_every_depth(
    db_session: AsyncSession,
) -> None:
    """Persisting a nested credential value would turn audit logs into a secret store."""
    actor_id = uuid4()
    project = Project(name="Audit redaction project")
    db_session.add(project)
    await db_session.commit()
    assert db_session.bind is not None
    event_id = await AuditService(async_sessionmaker(db_session.bind, expire_on_commit=False)).record(
        AuditEventInput(
            actor=Actor("user", actor_id, Role.OWNER, frozenset(), frozenset()),
            action="project.read",
            object_type="project",
            object_id=project.id,
            project_id=project.id,
            outcome="DENIED",
            request_id=uuid4(),
            metadata={
                "access_token": "top-secret",
                "nested": {
                    "refresh_token": "refresh-secret",
                    "items": [{"authorization": "Bearer secret"}, {"cookie": "session=secret"}],
                },
                "file_content": "private file",
                "model_input": "private prompt",
                "safe": "kept",
            },
        )
    )
    await db_session.commit()

    saved = await db_session.scalar(select(AuditLog).where(AuditLog.id == event_id))
    assert saved is not None
    assert saved.metadata_json == {
        "access_token": "[REDACTED]",
        "nested": {
            "refresh_token": "[REDACTED]",
            "items": [{"authorization": "[REDACTED]"}, {"cookie": "[REDACTED]"}],
        },
        "file_content": "[REDACTED]",
        "model_input": "[REDACTED]",
        "safe": "kept",
        "actor_role": "OWNER",
    }


def test_audit_metadata_rejects_non_json_objects() -> None:
    """Coercing arbitrary objects can persist opaque, non-portable audit metadata."""
    with pytest.raises(ValidationError, match="metadata must contain JSON values only"):
        AuditEventInput(
            actor=Actor("user", uuid4(), Role.OWNER, frozenset(), frozenset()),
            action="project.read",
            object_type="project",
            outcome="SUCCESS",
            request_id=uuid4(),
            metadata={"unserializable": object()},
        )
