"""PostgreSQL schema constraints for identity and project membership."""

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from superboss.modules.projects.models import Project, ProjectMember
from superboss.modules.users.models import Role, User, UserStatus


@pytest.mark.asyncio
async def test_database_rejects_a_second_owner(db_session: AsyncSession) -> None:
    """Removing the partial OWNER index would let two owners commit."""
    db_session.add(User(wecom_userid="owner-1", role=Role.OWNER, status=UserStatus.ACTIVE))
    await db_session.commit()

    db_session.add(User(wecom_userid="owner-2", role=Role.OWNER, status=UserStatus.ACTIVE))
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_database_rejects_duplicate_project_membership(db_session: AsyncSession) -> None:
    """Dropping the membership composite unique constraint would allow duplicates."""
    project = Project(name="Core platform")
    user = User(wecom_userid="staff-1", role=Role.STAFF, status=UserStatus.ACTIVE)
    db_session.add_all([project, user])
    await db_session.flush()

    db_session.add_all(
        [
            ProjectMember(project_id=project.id, user_id=user.id),
            ProjectMember(project_id=project.id, user_id=user.id),
        ]
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()
