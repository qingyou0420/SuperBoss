"""OWNER STAFF whitelist service contracts."""

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from superboss.core.actors import Actor
from superboss.core.errors import ConflictError, ForbiddenError, NotFoundError
from superboss.core.security import utcnow
from superboss.modules.audit.models import AuditLog
from superboss.modules.audit.service import AuditService
from superboss.modules.auth.models import AuthSession
from superboss.modules.auth.passwords import verify_password
from superboss.modules.projects.models import Project, ProjectMember
from superboss.modules.users.models import Role, User, UserStatus
from superboss.modules.users.schemas import ProjectAssignments, StaffCreate, StaffUpdate
from superboss.modules.users.service import OwnerUserService
from tests.identity import local_user


def owner_actor(user: User) -> Actor:
    return Actor("user", user.id, Role.OWNER, frozenset(), frozenset())


def staff_actor(user: User) -> Actor:
    return Actor("user", user.id, Role.STAFF, frozenset(), frozenset())


@pytest.mark.asyncio
async def test_create_always_makes_active_staff_and_assigns_each_existing_project(
    db_session: AsyncSession, active_owner: User
) -> None:
    projects = [Project(name="Alpha"), Project(name="Beta")]
    db_session.add_all(projects)
    await db_session.flush()
    service = OwnerUserService(db_session, AuditService(async_sessionmaker(db_session.bind, expire_on_commit=False)))

    created = await service.create_staff(
        owner_actor(active_owner),
        StaffCreate(username="staff-new", display_name="New staff", project_ids=[project.id for project in projects]),
        uuid4(),
    )

    assert (created.user.username, created.user.role, created.user.status) == (
        "staff-new",
        Role.STAFF,
        UserStatus.ACTIVE,
    )
    persisted = await db_session.get(User, created.user.id)
    assert persisted is not None and persisted.must_change_password is True
    assert verify_password(persisted.password_hash, created.temporary_password).valid
    assert created.temporary_password not in repr(created)
    assert set((await db_session.scalars(select(ProjectMember.project_id).where(ProjectMember.user_id == created.user.id))).all()) == {project.id for project in projects}


@pytest.mark.asyncio
async def test_staff_cannot_use_any_owner_user_service_operation(
    db_session: AsyncSession, active_owner: User
) -> None:
    staff = local_user("staff-1", display_name="Staff")
    db_session.add(staff)
    await db_session.flush()
    service = OwnerUserService(db_session, None)

    with pytest.raises(ForbiddenError):
        await service.list_users(staff_actor(staff), uuid4())
    with pytest.raises(ForbiddenError):
        await service.update_staff(staff_actor(staff), staff.id, StaffUpdate(display_name="x"), uuid4())
    with pytest.raises(ForbiddenError):
        await service.replace_projects(staff_actor(staff), staff.id, ProjectAssignments(project_ids=[]), uuid4())


@pytest.mark.asyncio
async def test_owner_target_is_rejected_by_every_staff_mutation(
    db_session: AsyncSession, active_owner: User
) -> None:
    service = OwnerUserService(db_session, None)
    owner = owner_actor(active_owner)

    with pytest.raises(ConflictError):
        await service.update_staff(owner, active_owner.id, StaffUpdate(status=UserStatus.DISABLED), uuid4())
    with pytest.raises(ConflictError):
        await service.replace_projects(owner, active_owner.id, ProjectAssignments(project_ids=[]), uuid4())


@pytest.mark.asyncio
async def test_disabling_staff_revokes_every_browser_session_in_same_business_transaction(
    db_session: AsyncSession, active_owner: User
) -> None:
    staff = local_user("staff-1", display_name="Staff")
    db_session.add(staff)
    await db_session.flush()
    now = utcnow()
    db_session.add_all([
        AuthSession(user_id=staff.id, access_jti="a" * 32, refresh_token_hash="b" * 64, access_expires_at=now + timedelta(hours=1), refresh_expires_at=now + timedelta(days=1)),
        AuthSession(user_id=staff.id, access_jti="c" * 32, refresh_token_hash="d" * 64, access_expires_at=now + timedelta(hours=1), refresh_expires_at=now + timedelta(days=1)),
    ])
    await db_session.flush()
    service = OwnerUserService(db_session, None)

    await service.update_staff(owner_actor(active_owner), staff.id, StaffUpdate(status=UserStatus.DISABLED), uuid4())

    assert staff.status == UserStatus.DISABLED
    assert all(item.revoked_at is not None for item in (await db_session.scalars(select(AuthSession).where(AuthSession.user_id == staff.id))).all())


@pytest.mark.asyncio
async def test_project_replace_is_all_or_nothing_when_any_project_is_unknown(
    db_session: AsyncSession, active_owner: User
) -> None:
    staff = local_user("staff-1", display_name="Staff")
    old = Project(name="Old")
    valid = Project(name="Valid")
    db_session.add_all([staff, old, valid])
    await db_session.flush()
    db_session.add(ProjectMember(user_id=staff.id, project_id=old.id))
    await db_session.flush()
    service = OwnerUserService(db_session, None)

    with pytest.raises(NotFoundError):
        await service.replace_projects(owner_actor(active_owner), staff.id, ProjectAssignments(project_ids=[valid.id, uuid4()]), uuid4())

    assert (await db_session.scalars(select(ProjectMember.project_id).where(ProjectMember.user_id == staff.id))).all() == [old.id]


@pytest.mark.asyncio
async def test_denied_events_are_bounded_and_success_audit_is_only_written_after_commit(
    db_session: AsyncSession, active_owner: User
) -> None:
    service = OwnerUserService(db_session, AuditService(async_sessionmaker(db_session.bind, expire_on_commit=False)))
    request_id = uuid4()
    with pytest.raises(ForbiddenError):
        await service.list_users(Actor("device", uuid4(), None, frozenset(), frozenset()), request_id)
    denied = await db_session.scalar(select(AuditLog).where(AuditLog.request_id == request_id))
    assert denied is not None and denied.outcome == "DENIED"
    assert denied.metadata_json == {"actor_role": None, "reason": "OWNER_REQUIRED"}

    created = await service.create_staff(owner_actor(active_owner), StaffCreate(username="staff-audit", display_name="Audit", project_ids=[]), uuid4())
    await service.commit_and_record_success(owner_actor(active_owner), "user.create", uuid4(), created.user.id)


@pytest.mark.asyncio
async def test_password_reset_rotates_secret_and_revokes_all_sessions(
    db_session: AsyncSession, active_owner: User
) -> None:
    staff = local_user("staff-reset", display_name="Reset target")
    db_session.add(staff)
    await db_session.flush()
    original_hash = staff.password_hash
    now = utcnow()
    db_session.add(
        AuthSession(
            user_id=staff.id,
            access_jti="e" * 32,
            refresh_token_hash="f" * 64,
            access_expires_at=now + timedelta(hours=1),
            refresh_expires_at=now + timedelta(days=1),
        )
    )
    await db_session.flush()
    service = OwnerUserService(db_session, None)

    reset = await service.reset_staff_password(
        owner_actor(active_owner), staff.id, uuid4()
    )

    assert staff.password_hash != original_hash
    assert staff.must_change_password is True
    assert verify_password(staff.password_hash, reset.temporary_password).valid
    assert reset.temporary_password not in repr(reset)
    sessions = (
        await db_session.scalars(select(AuthSession).where(AuthSession.user_id == staff.id))
    ).all()
    assert sessions and all(item.revoked_at is not None for item in sessions)


@pytest.mark.asyncio
async def test_owner_password_cannot_be_reset_by_owner_web_service(
    db_session: AsyncSession, active_owner: User
) -> None:
    service = OwnerUserService(db_session, None)

    with pytest.raises(ConflictError):
        await service.reset_staff_password(
            owner_actor(active_owner), active_owner.id, uuid4()
        )
