"""HEAD-level file/upload constraint coverage."""

from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from superboss.modules.files.models import File, FileState, Upload
from superboss.modules.projects.models import Project
from superboss.modules.users.models import User


@pytest.mark.asyncio
async def test_database_rejects_upload_referencing_file_from_other_project(
    db_session: AsyncSession, active_owner: User
) -> None:
    """A cross-project Upload/File pair must be impossible even for direct database writers."""
    project_a = Project(name="File project A")
    project_b = Project(name="File project B")
    db_session.add_all([project_a, project_b])
    await db_session.flush()
    file = File(
        project_id=project_a.id,
        filename="a.pdf",
        category="资料",
        file_date=date(2026, 8, 9),
        object_key=f"projects/{project_a.id}/a.pdf",
        size_bytes=1,
        sha256="0" * 64,
        state=FileState.UPLOADING,
        uploader_id=active_owner.id,
        uploader_kind="user",
        content_type="application/pdf",
    )
    db_session.add(file)
    await db_session.flush()
    db_session.add(
        Upload(
            file_id=file.id,
            project_id=project_b.id,
            uploader_id=active_owner.id,
            uploader_kind="user",
            idempotency_key="cross-project",
            metadata_fingerprint="0" * 64,
            multipart_id="multipart-cross-project",
        )
    )

    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()
