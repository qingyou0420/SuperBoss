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
from superboss.modules.audit.models import AuditLog
from superboss.modules.auth.models import AuthSession
from superboss.modules.auth.passwords import verify_password
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
async def test_owner_creates_and_lists_staff_with_strict_contracts(owner_users_client: TestClient, db_session: AsyncSession) -> None:
    login(owner_users_client)
    created = owner_users_client.post(
        "/api/v1/owner/users",
        json={"username": "staff-acceptance", "display_name": "Acceptance"},
        headers=csrf(owner_users_client),
    )
    assert created.status_code == 201
    body = created.json()
    assert set(body) == {"temporary_password", "user"}
    assert set(body["user"]) == {
        "id",
        "username",
        "display_name",
        "role",
        "status",
        "last_login_at",
    }
    assert body["user"]["username"] == "staff-acceptance" and body["user"]["role"] == "STAFF" and body["user"]["status"] == "ACTIVE"
    persisted = await db_session.scalar(select(User).where(User.username == "staff-acceptance"))
    assert persisted is not None
    assert verify_password(persisted.password_hash, body["temporary_password"]).valid
    assert "password" not in str(body["user"]).lower()
    listed = owner_users_client.get("/api/v1/owner/users")
    assert listed.status_code == 200
    assert any(item["username"] == "staff-acceptance" for item in listed.json())
    gone = owner_users_client.put(
        f"/api/v1/owner/users/{body['user']['id']}/projects",
        json={"project_ids": []},
        headers=csrf(owner_users_client),
    )
    assert gone.status_code == 404


@pytest.mark.asyncio
async def test_user_routes_reject_staff_duplicate_role_and_owner_mutation(owner_users_client: TestClient, db_session: AsyncSession) -> None:
    staff = local_user("staff-1", display_name="Staff")
    db_session.add(staff)
    await db_session.commit()
    login(owner_users_client, "staff-1")
    error(owner_users_client.get("/api/v1/owner/users"), 403, "OWNER_REQUIRED")
    owner_users_client.cookies.clear()
    login(owner_users_client)
    error(owner_users_client.post("/api/v1/owner/users", json={"username": "staff-1", "display_name": "Again"}, headers=csrf(owner_users_client)), 409, "USERNAME_CONFLICT")
    invalid = owner_users_client.post("/api/v1/owner/users", json={"username": "staff-2", "display_name": "Bad", "role": "OWNER"}, headers=csrf(owner_users_client))
    error(invalid, 422, "VALIDATION_ERROR")
    extra = owner_users_client.post(
        "/api/v1/owner/users",
        json={"username": "staff-3", "display_name": "Extra", "project_ids": []},
        headers=csrf(owner_users_client),
    )
    error(extra, 422, "VALIDATION_ERROR")
    owner = await db_session.scalar(select(User).where(User.username == "owner"))
    assert owner is not None
    error(owner_users_client.patch(f"/api/v1/owner/users/{owner.id}", json={"status": "DISABLED"}, headers=csrf(owner_users_client)), 409, "OWNER_PROTECTED")


@pytest.mark.asyncio
async def test_staff_is_denied_from_every_owner_user_route_with_bounded_audit(
    owner_users_client: TestClient, db_session: AsyncSession
) -> None:
    staff = local_user("staff-1", display_name="Staff")
    target = local_user("staff-target", display_name="Target")
    db_session.add_all([staff, target])
    await db_session.commit()
    login(owner_users_client, "staff-1")

    routes = [
        ("get", "/api/v1/owner/users", None, "user.list", None),
        ("post", "/api/v1/owner/users", {"username": "blocked-create", "display_name": "Blocked"}, "user.create", None),
        ("patch", f"/api/v1/owner/users/{target.id}", {"display_name": "Blocked update"}, "user.update", target.id),
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
async def test_disable_revokes_sessions(owner_users_client: TestClient, db_session: AsyncSession) -> None:
    staff = local_user("staff-1", display_name="Staff")
    db_session.add(staff)
    await db_session.commit()
    login(owner_users_client)
    disabled = owner_users_client.patch(f"/api/v1/owner/users/{staff.id}", json={"status": "DISABLED"}, headers=csrf(owner_users_client))
    assert disabled.status_code == 200
    assert all(item.revoked_at is not None for item in (await db_session.scalars(select(AuthSession).where(AuthSession.user_id == staff.id))).all())


def test_owner_user_writes_retain_browser_csrf_and_anonymous_401(owner_users_client: TestClient) -> None:
    error(owner_users_client.post("/api/v1/owner/users", json={"username": "new", "display_name": "New"}), 401, "AUTHENTICATION_REQUIRED")
    login(owner_users_client)
    error(owner_users_client.post("/api/v1/owner/users", json={"username": "new", "display_name": "New"}), 403, "CSRF_VALIDATION_FAILED")


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
