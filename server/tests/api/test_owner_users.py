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
from superboss.modules.auth.passwords import verify_password
from superboss.modules.projects.models import Project, ProjectMember
from superboss.modules.users.models import Role, User
from tests.identity import LOCAL_TEST_PASSWORD, local_user


@pytest_asyncio.fixture
async def owner_users_client(
    db_session: AsyncSession, test_settings: Settings, active_owner: User
):
    del active_owner
    await db_session.commit()
    app = create_app(test_settings)
    with TestClient(app, base_url="https://testserver") as client:
        yield client


def login(client: TestClient, username: str = "owner") -> None:
    assert client.get("/api/v1/auth/csrf").status_code == 204
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": LOCAL_TEST_PASSWORD},
        headers=csrf(client),
    )
    assert response.status_code == 204


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
    login(owner_users_client)
    created = owner_users_client.post("/api/v1/owner/users", json={"username": "staff-acceptance", "display_name": "Acceptance", "project_ids": [str(item.id) for item in projects]}, headers=csrf(owner_users_client))
    assert created.status_code == 201
    body = created.json()
    assert set(body) == {"temporary_password", "user"}
    assert body["user"]["username"] == "staff-acceptance" and body["user"]["role"] == "STAFF" and body["user"]["status"] == "ACTIVE"
    assert {item["id"] for item in body["user"]["projects"]} == {str(item.id) for item in projects}
    persisted = await db_session.scalar(select(User).where(User.username == "staff-acceptance"))
    assert persisted is not None
    assert verify_password(persisted.password_hash, body["temporary_password"]).valid
    assert "password" not in str(body["user"]).lower()
    assert owner_users_client.get("/api/v1/owner/users").status_code == 200
    reassigned = owner_users_client.put(f"/api/v1/owner/users/{body['user']['id']}/projects", json={"project_ids": [str(projects[0].id)]}, headers=csrf(owner_users_client))
    assert reassigned.status_code == 200 and [item["id"] for item in reassigned.json()["projects"]] == [str(projects[0].id)]


@pytest.mark.asyncio
async def test_user_routes_reject_staff_duplicate_role_and_owner_mutation(owner_users_client: TestClient, db_session: AsyncSession) -> None:
    staff = local_user("staff-1", display_name="Staff")
    db_session.add(staff)
    await db_session.commit()
    login(owner_users_client, "staff-1")
    error(owner_users_client.get("/api/v1/owner/users"), 403, "OWNER_REQUIRED")
    owner_users_client.cookies.clear()
    login(owner_users_client)
    error(owner_users_client.post("/api/v1/owner/users", json={"username": "staff-1", "display_name": "Again", "project_ids": []}, headers=csrf(owner_users_client)), 409, "USERNAME_CONFLICT")
    invalid = owner_users_client.post("/api/v1/owner/users", json={"username": "staff-2", "display_name": "Bad", "project_ids": [], "role": "OWNER"}, headers=csrf(owner_users_client))
    error(invalid, 422, "VALIDATION_ERROR")
    owner = await db_session.scalar(select(User).where(User.username == "owner"))
    assert owner is not None
    error(owner_users_client.patch(f"/api/v1/owner/users/{owner.id}", json={"status": "DISABLED"}, headers=csrf(owner_users_client)), 409, "OWNER_PROTECTED")


@pytest.mark.asyncio
async def test_staff_is_denied_from_every_owner_user_route_with_bounded_audit(
    owner_users_client: TestClient, db_session: AsyncSession
) -> None:
    staff = local_user("staff-1", display_name="Staff")
    target = local_user("staff-target", display_name="Target")
    project = Project(name="Staff denial project")
    db_session.add_all([staff, target, project])
    await db_session.commit()
    login(owner_users_client, "staff-1")

    routes = [
        ("get", "/api/v1/owner/users", None, "user.list", None),
        ("post", "/api/v1/owner/users", {"username": "blocked-create", "display_name": "Blocked", "project_ids": []}, "user.create", None),
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
    assert len(StaffCreate(username="bounded", display_name="Bounded", project_ids=accepted).project_ids) == 1000
    assert len(ProjectAssignments(project_ids=accepted).project_ids) == 1000
    target = local_user("bounded-target", display_name="Bounded target")
    db_session.add(target)
    await db_session.commit()
    login(owner_users_client)
    too_many = [str(uuid4()) for _ in range(1001)]
    create_request_id = uuid4()
    create_response = owner_users_client.post(
        "/api/v1/owner/users",
        json={"username": "too-many", "display_name": "Too many", "project_ids": too_many},
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
    from superboss.modules.users.schemas import ProjectAssignments
    from superboss.modules.users.service import OwnerUserService

    staff = local_user("concurrent-staff", display_name="Concurrent")
    projects = [Project(name=f"Concurrent {index}") for index in range(3)]
    db_session.add_all([staff, *projects])
    await db_session.commit()
    staff_id = staff.id
    actor = Actor(active_owner.id, Role.OWNER)
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    assignments = ([projects[0].id, projects[1].id], [projects[2].id])
    request_ids = (uuid4(), uuid4())
    gate = asyncio.Event()

    async def replace(project_ids: list[UUID], request_id: UUID) -> None:
        await gate.wait()
        async with session_factory() as session:
            service = OwnerUserService(session, AuditService(session_factory))
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
    staff = local_user("staff-1", display_name="Staff")
    old = Project(name="Old")
    valid = Project(name="Valid")
    db_session.add_all([staff, old, valid])
    await db_session.flush()
    db_session.add(ProjectMember(user_id=staff.id, project_id=old.id))
    await db_session.commit()
    login(owner_users_client)
    unknown = owner_users_client.put(f"/api/v1/owner/users/{staff.id}/projects", json={"project_ids": [str(valid.id), str(uuid4())]}, headers=csrf(owner_users_client))
    error(unknown, 404, "PROJECT_NOT_FOUND")
    assert (await db_session.scalars(select(ProjectMember.project_id).where(ProjectMember.user_id == staff.id))).all() == [old.id]
    disabled = owner_users_client.patch(f"/api/v1/owner/users/{staff.id}", json={"status": "DISABLED"}, headers=csrf(owner_users_client))
    assert disabled.status_code == 200
    assert all(item.revoked_at is not None for item in (await db_session.scalars(select(AuthSession).where(AuthSession.user_id == staff.id))).all())


def test_owner_user_writes_retain_browser_csrf_and_anonymous_401(owner_users_client: TestClient) -> None:
    error(owner_users_client.post("/api/v1/owner/users", json={"username": "new", "display_name": "New", "project_ids": []}), 401, "AUTHENTICATION_REQUIRED")
    login(owner_users_client)
    error(owner_users_client.post("/api/v1/owner/users", json={"username": "new", "display_name": "New", "project_ids": []}), 403, "CSRF_VALIDATION_FAILED")


@pytest.mark.asyncio
async def test_owner_resets_staff_password_once_but_cannot_reset_owner(
    owner_users_client: TestClient, db_session: AsyncSession
) -> None:
    staff = local_user("staff-reset", display_name="Reset")
    db_session.add(staff)
    await db_session.commit()
    original_hash = staff.password_hash
    login(owner_users_client)

    response = owner_users_client.post(
        f"/api/v1/owner/users/{staff.id}/password-reset",
        headers=csrf(owner_users_client),
    )

    assert response.status_code == 200
    assert set(response.json()) == {"temporary_password"}
    await db_session.refresh(staff)
    assert staff.password_hash != original_hash
    assert verify_password(staff.password_hash, response.json()["temporary_password"]).valid
    owner = await db_session.scalar(select(User).where(User.role == Role.OWNER))
    assert owner is not None
    error(
        owner_users_client.post(
            f"/api/v1/owner/users/{owner.id}/password-reset",
            headers=csrf(owner_users_client),
        ),
        409,
        "OWNER_PROTECTED",
    )
