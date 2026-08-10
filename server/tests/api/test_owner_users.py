"""HTTP contracts for OWNER-managed STAFF users."""

import asyncio
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from superboss.core.config import Settings
from superboss.main import create_app
from superboss.modules.audit.models import AuditLog
from superboss.modules.auth.models import AuthSession
from superboss.modules.projects.models import Project, ProjectMember
from superboss.modules.users.models import Role, User, UserStatus


@pytest_asyncio.fixture
async def owner_users_client(db_session: AsyncSession, test_settings: Settings):
    app = create_app(test_settings)
    with TestClient(app, base_url="https://testserver") as client:
        yield client


def login(client: TestClient, code: str) -> None:
    started = client.get("/api/v1/auth/wecom/start")
    assert client.get("/api/v1/auth/wecom/callback", params={"code": code, "state": started.json()["state"]}).status_code == 204


def csrf(client: TestClient) -> dict[str, str]:
    return {"X-CSRF-Token": str(client.cookies.get("XSRF-TOKEN"))}


def error(response: httpx.Response, status: int, code: str) -> None:
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert set(response.json()["error"]) == {"code", "message", "request_id"}


@pytest.mark.asyncio
async def test_owner_creates_lists_and_assigns_staff_with_strict_contracts(owner_users_client: TestClient, db_session: AsyncSession) -> None:
    projects = [Project(name="Alpha"), Project(name="Beta")]
    db_session.add_all(projects)
    await db_session.commit()
    login(owner_users_client, "owner-code")
    created = owner_users_client.post("/api/v1/owner/users", json={"wecom_userid": "staff-acceptance", "display_name": "Acceptance", "project_ids": [str(item.id) for item in projects]}, headers=csrf(owner_users_client))
    assert created.status_code == 201
    body = created.json()
    assert body["wecom_userid"] == "staff-acceptance" and body["role"] == "STAFF" and body["status"] == "ACTIVE"
    assert {item["id"] for item in body["projects"]} == {str(item.id) for item in projects}
    assert owner_users_client.get("/api/v1/owner/users").status_code == 200
    reassigned = owner_users_client.put(f"/api/v1/owner/users/{body['id']}/projects", json={"project_ids": [str(projects[0].id)]}, headers=csrf(owner_users_client))
    assert reassigned.status_code == 200 and [item["id"] for item in reassigned.json()["projects"]] == [str(projects[0].id)]


@pytest.mark.asyncio
async def test_user_routes_reject_staff_duplicate_role_and_owner_mutation(owner_users_client: TestClient, db_session: AsyncSession) -> None:
    staff = User(wecom_userid="staff-1", display_name="Staff", role=Role.STAFF, status=UserStatus.ACTIVE)
    db_session.add(staff)
    await db_session.commit()
    login(owner_users_client, "staff-code")
    error(owner_users_client.get("/api/v1/owner/users"), 403, "OWNER_REQUIRED")
    owner_users_client.cookies.clear()
    login(owner_users_client, "owner-code")
    error(owner_users_client.post("/api/v1/owner/users", json={"wecom_userid": "staff-1", "display_name": "Again", "project_ids": []}, headers=csrf(owner_users_client)), 409, "USERID_CONFLICT")
    invalid = owner_users_client.post("/api/v1/owner/users", json={"wecom_userid": "staff-2", "display_name": "Bad", "project_ids": [], "role": "OWNER"}, headers=csrf(owner_users_client))
    error(invalid, 422, "VALIDATION_ERROR")
    owner = await db_session.scalar(select(User).where(User.wecom_userid == "owner-1"))
    assert owner is not None
    error(owner_users_client.patch(f"/api/v1/owner/users/{owner.id}", json={"status": "DISABLED"}, headers=csrf(owner_users_client)), 409, "OWNER_PROTECTED")


@pytest.mark.asyncio
async def test_staff_is_denied_from_every_owner_user_route_with_bounded_audit(
    owner_users_client: TestClient, db_session: AsyncSession
) -> None:
    staff = User(wecom_userid="staff-1", display_name="Staff", role=Role.STAFF, status=UserStatus.ACTIVE)
    target = User(wecom_userid="staff-target", display_name="Target", role=Role.STAFF, status=UserStatus.ACTIVE)
    project = Project(name="Staff denial project")
    db_session.add_all([staff, target, project])
    await db_session.commit()
    login(owner_users_client, "staff-code")

    routes = [
        ("get", "/api/v1/owner/users", None, "user.list", None),
        ("post", "/api/v1/owner/users", {"wecom_userid": "blocked-create", "display_name": "Blocked", "project_ids": []}, "user.create", None),
        ("patch", f"/api/v1/owner/users/{target.id}", {"display_name": "Blocked update"}, "user.update", target.id),
        ("put", f"/api/v1/owner/users/{target.id}/projects", {"project_ids": [str(project.id)]}, "user.projects.replace", target.id),
    ]
    for method, path, body, action, object_id in routes:
        request_id = uuid4()
        request = getattr(owner_users_client, method)
        headers = {**csrf(owner_users_client), "X-Request-ID": str(request_id)}
        response = request(path, headers=headers) if body is None else request(path, json=body, headers=headers)
        error(response, 403, "OWNER_REQUIRED")
        event = await db_session.scalar(select(AuditLog).where(AuditLog.request_id == request_id))
        assert event is not None
        assert (event.action, event.outcome, event.object_id, event.metadata_json) == (
            action, "DENIED", object_id, {"actor_role": "STAFF", "reason": "OWNER_REQUIRED"}
        )
        assert "staff-1" not in str(event.metadata_json)


@pytest.mark.asyncio
async def test_project_id_collections_are_bounded_before_repository_or_audit(
    owner_users_client: TestClient, db_session: AsyncSession
) -> None:
    from superboss.modules.users.schemas import ProjectAssignments, StaffCreate

    accepted = [uuid4() for _ in range(1000)]
    assert len(StaffCreate(wecom_userid="bounded", display_name="Bounded", project_ids=accepted).project_ids) == 1000
    assert len(ProjectAssignments(project_ids=accepted).project_ids) == 1000
    target = User(wecom_userid="bounded-target", display_name="Bounded target", role=Role.STAFF, status=UserStatus.ACTIVE)
    db_session.add(target)
    await db_session.commit()
    login(owner_users_client, "owner-code")
    too_many = [str(uuid4()) for _ in range(1001)]
    create_request_id = uuid4()
    create_response = owner_users_client.post(
        "/api/v1/owner/users",
        json={"wecom_userid": "too-many", "display_name": "Too many", "project_ids": too_many},
        headers={**csrf(owner_users_client), "X-Request-ID": str(create_request_id)},
    )
    error(create_response, 422, "VALIDATION_ERROR")
    assert await db_session.scalar(select(AuditLog).where(AuditLog.request_id == create_request_id)) is None
    replace_request_id = uuid4()
    replace_response = owner_users_client.put(
        f"/api/v1/owner/users/{target.id}/projects",
        json={"project_ids": too_many},
        headers={**csrf(owner_users_client), "X-Request-ID": str(replace_request_id)},
    )
    error(replace_response, 422, "VALIDATION_ERROR")
    assert await db_session.scalar(select(AuditLog).where(AuditLog.request_id == replace_request_id)) is None


@pytest.mark.asyncio
async def test_concurrent_project_replaces_are_serialized_to_one_complete_membership_set(
    db_session: AsyncSession, active_owner: User
) -> None:
    from superboss.core.actors import Actor
    from superboss.modules.audit.service import AuditService
    from superboss.modules.users.repository import UserRepository
    from superboss.modules.users.schemas import ProjectAssignments
    from superboss.modules.users.service import OwnerUserService

    staff = User(wecom_userid="concurrent-staff", display_name="Concurrent", role=Role.STAFF, status=UserStatus.ACTIVE)
    projects = [Project(name=f"Concurrent {index}") for index in range(3)]
    db_session.add_all([staff, *projects])
    await db_session.commit()
    staff_id = staff.id
    actor = Actor("user", active_owner.id, Role.OWNER, frozenset(), frozenset())
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    assignments = ([projects[0].id, projects[1].id], [projects[2].id])
    request_ids = (uuid4(), uuid4())
    gate = asyncio.Event()

    async def replace(project_ids: list[UUID], request_id: UUID) -> None:
        await gate.wait()
        async with session_factory() as session:
            service = OwnerUserService(UserRepository(session), AuditService(session_factory))
            await service.replace_projects(actor, staff_id, ProjectAssignments(project_ids=project_ids), request_id)
            await service.commit_and_record_success(actor, "user.projects.replace", request_id, staff_id)

    first = asyncio.create_task(replace(*assignments[:1], request_ids[0]))
    second = asyncio.create_task(replace(*assignments[1:], request_ids[1]))
    gate.set()
    await asyncio.wait_for(asyncio.gather(first, second), timeout=10)

    db_session.expire_all()
    final_ids = set((await db_session.scalars(select(ProjectMember.project_id).where(ProjectMember.user_id == staff_id))).all())
    assert final_ids in (set(assignments[0]), set(assignments[1]))
    assert len(final_ids) in {1, 2}
    events = list((await db_session.scalars(select(AuditLog).where(AuditLog.request_id.in_(request_ids)))).all())
    assert len(events) == 2
    assert all(event.action == "user.projects.replace" and event.outcome == "SUCCESS" for event in events)


@pytest.mark.asyncio
async def test_disable_revokes_sessions_and_unknown_project_does_not_partially_replace(owner_users_client: TestClient, db_session: AsyncSession) -> None:
    staff = User(wecom_userid="staff-1", display_name="Staff", role=Role.STAFF, status=UserStatus.ACTIVE)
    old = Project(name="Old")
    valid = Project(name="Valid")
    db_session.add_all([staff, old, valid])
    await db_session.flush()
    db_session.add(ProjectMember(user_id=staff.id, project_id=old.id))
    await db_session.commit()
    login(owner_users_client, "owner-code")
    unknown = owner_users_client.put(f"/api/v1/owner/users/{staff.id}/projects", json={"project_ids": [str(valid.id), str(uuid4())]}, headers=csrf(owner_users_client))
    error(unknown, 404, "PROJECT_NOT_FOUND")
    assert (await db_session.scalars(select(ProjectMember.project_id).where(ProjectMember.user_id == staff.id))).all() == [old.id]
    disabled = owner_users_client.patch(f"/api/v1/owner/users/{staff.id}", json={"status": "DISABLED"}, headers=csrf(owner_users_client))
    assert disabled.status_code == 200
    assert all(item.revoked_at is not None for item in (await db_session.scalars(select(AuthSession).where(AuthSession.user_id == staff.id))).all())


def test_owner_user_writes_retain_browser_csrf_and_anonymous_401(owner_users_client: TestClient) -> None:
    error(owner_users_client.post("/api/v1/owner/users", json={"wecom_userid": "new", "display_name": "New", "project_ids": []}), 401, "AUTHENTICATION_REQUIRED")
    login(owner_users_client, "owner-code")
    error(owner_users_client.post("/api/v1/owner/users", json={"wecom_userid": "new", "display_name": "New", "project_ids": []}), 403, "CSRF_VALIDATION_FAILED")
