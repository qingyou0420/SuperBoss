"""HEAD-level file constraint coverage."""

from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from superboss.modules.files.models import File, FileState
from superboss.modules.projects.models import Project
from superboss.modules.users.models import User


@pytest.mark.asyncio
async def test_database_rejects_duplicate_idempotency_with_changed_fingerprint(
    db_session: AsyncSession, active_owner: User
) -> None:
    """The merged File unique key still rejects a conflicting replay."""
    project = Project(name="File project")
    db_session.add(project)
    await db_session.flush()
    db_session.add(
        File(
            project_id=project.id,
            filename="a.pdf",
            category="资料",
            file_date=date(2026, 8, 9),
            object_key=f"projects/{project.id}/a.pdf",
            size_bytes=1,
            sha256="0" * 64,
            state=FileState.UPLOADING,
            uploader_id=active_owner.id,
            uploader_kind="user",
            content_type="application/pdf",
            idempotency_key="same-key",
            metadata_fingerprint="a" * 64,
        )
    )
    await db_session.flush()
    db_session.add(
        File(
            project_id=project.id,
            filename="b.pdf",
            category="资料",
            file_date=date(2026, 8, 9),
            object_key=f"projects/{project.id}/b.pdf",
            size_bytes=1,
            sha256="1" * 64,
            state=FileState.UPLOADING,
            uploader_id=active_owner.id,
            uploader_kind="user",
            content_type="application/pdf",
            idempotency_key="same-key",
            metadata_fingerprint="b" * 64,
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()
