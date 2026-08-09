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
    with pytest.raises(ValidationError, match="invalid audit metadata"):
        AuditEventInput(
            actor=Actor("user", uuid4(), Role.OWNER, frozenset(), frozenset()),
            action="project.read",
            object_type="project",
            outcome="SUCCESS",
            request_id=uuid4(),
            metadata={"unserializable": object()},
        )


@pytest.mark.parametrize(
    "key",
    [
        "ACCESS_TOKEN",
        "acce\N{LATIN SMALL LETTER LONG S}\N{LATIN SMALL LETTER LONG S}_token",
        "\N{FULLWIDTH LATIN SMALL LETTER A}\N{FULLWIDTH LATIN SMALL LETTER C}\N{FULLWIDTH LATIN SMALL LETTER C}\N{FULLWIDTH LATIN SMALL LETTER E}\N{FULLWIDTH LATIN SMALL LETTER S}\N{FULLWIDTH LATIN SMALL LETTER S}_\N{FULLWIDTH LATIN SMALL LETTER T}\N{FULLWIDTH LATIN SMALL LETTER O}\N{FULLWIDTH LATIN SMALL LETTER K}\N{FULLWIDTH LATIN SMALL LETTER E}\N{FULLWIDTH LATIN SMALL LETTER N}",
    ],
)
def test_audit_metadata_redacts_unicode_equivalent_sensitive_keys(key: str) -> None:
    """Unicode compatibility spellings must not bypass credential redaction."""
    event = AuditEventInput(
        actor=Actor("user", uuid4(), Role.OWNER, frozenset(), frozenset()),
        action="project.read",
        object_type="project",
        outcome="SUCCESS",
        request_id=uuid4(),
        metadata={"nested": [{key: "ORIGINAL-SECRET"}]},
    )
    assert event.metadata == {"nested": [{key: "[REDACTED]"}]}


@pytest.mark.parametrize("metadata", [{"loop": None}, {"deep": {}}])
def test_audit_metadata_rejects_cycles_and_excessive_depth(metadata: dict[str, object]) -> None:
    """Unbounded recursive inputs must become validation failures, never interpreter recursion errors."""
    if "loop" in metadata:
        metadata["loop"] = metadata
    else:
        cursor = metadata["deep"]
        assert isinstance(cursor, dict)
        for _ in range(33):
            next_value: dict[str, object] = {}
            cursor["deep"] = next_value
            cursor = next_value
    with pytest.raises(ValidationError):
        AuditEventInput(
            actor=Actor("user", uuid4(), Role.OWNER, frozenset(), frozenset()),
            action="project.read",
            object_type="project",
            outcome="SUCCESS",
            request_id=uuid4(),
            metadata=metadata,
        )
