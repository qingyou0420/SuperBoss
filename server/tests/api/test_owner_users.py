"""HTTP contracts for OWNER-managed STAFF users."""

from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from superboss.core.config import Settings
from superboss.main import create_app
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
