"""Real HTTP and PostgreSQL project authorization tests."""

import asyncio
from collections.abc import AsyncIterator
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from superboss.core.config import Settings
from superboss.main import create_app
from superboss.modules.auth.models import AuthSession, OAuthState
from superboss.modules.auth.repository import AuthRepository
from superboss.modules.auth.service import AuthService
from superboss.modules.projects.models import Project, ProjectMember
from superboss.modules.users.models import Role, User, UserStatus
from superboss.modules.users.repository import UserRepository


@pytest_asyncio.fixture
async def api_client(
    db_session: AsyncSession, test_settings: Settings
) -> AsyncIterator[TestClient]:
    app = create_app(test_settings)
    with TestClient(app, base_url="https://testserver") as client:
        yield client
    await db_session.rollback()
    await db_session.execute(delete(ProjectMember))
    await db_session.execute(delete(Project))
    await db_session.execute(delete(OAuthState))
    await db_session.execute(delete(AuthSession))
    await db_session.execute(delete(User))
    await db_session.commit()


def _login(client: TestClient, code: str) -> None:
    started = client.get("/api/v1/auth/wecom/start")
    response = client.get(
        "/api/v1/auth/wecom/callback", params={"code": code, "state": started.json()["state"]}
    )
    assert response.status_code == 204


def _csrf_headers(client: TestClient) -> dict[str, str]:
    return {"X-CSRF-Token": str(client.cookies.get("XSRF-TOKEN"))}


@pytest.mark.asyncio
async def test_owner_sees_all_projects_and_create_preserves_is_test(
    api_client: TestClient, db_session: AsyncSession
) -> None:
    """Filtering owners by memberships or dropping is_test loses visible project state."""
    db_session.add(Project(name="Existing", is_test=False))
    await db_session.commit()
    _login(api_client, "owner-code")

    created = api_client.post(
        "/api/v1/projects", json={"name": "Sandbox", "is_test": True}, headers=_csrf_headers(api_client)
    )
    assert created.status_code == 201
    assert created.json()["name"] == "Sandbox"
    assert created.json()["is_test"] is True

    listed = api_client.get("/api/v1/projects")
    assert listed.status_code == 200
    assert {project["name"] for project in listed.json()} == {"Existing", "Sandbox"}


@pytest.mark.asyncio
async def test_staff_sees_only_memberships_and_cannot_create(
    api_client: TestClient, db_session: AsyncSession
) -> None:
    """Removing the membership join would reveal Hidden to an unrelated STAFF account."""
    staff = User(wecom_userid="staff-1", display_name="Staff", role=Role.STAFF, status=UserStatus.ACTIVE)
    assigned = Project(name="Assigned")
    hidden = Project(name="Hidden")
    db_session.add_all([staff, assigned, hidden])
    await db_session.flush()
    db_session.add(ProjectMember(project_id=assigned.id, user_id=staff.id))
    await db_session.commit()
    _login(api_client, "staff-code")

    listed = api_client.get("/api/v1/projects")
    assert listed.status_code == 200
    assert [project["id"] for project in listed.json()] == [str(assigned.id)]
    assigned_detail = api_client.get(f"/api/v1/projects/{assigned.id}")
    assert assigned_detail.status_code == 200
    assert assigned_detail.json()["name"] == "Assigned"
    assert api_client.get(f"/api/v1/projects/{hidden.id}").status_code == 403
    assert api_client.post(
        "/api/v1/projects", json={"name": "Denied"}, headers=_csrf_headers(api_client)
    ).status_code == 403


@pytest.mark.asyncio
async def test_cookie_actor_precedes_bearer_without_fallback(
    api_client: TestClient, db_session: AsyncSession
) -> None:
    """Header precedence or fallback would let a second credential change the browser actor."""
    staff = User(wecom_userid="staff-1", display_name="Staff", role=Role.STAFF, status=UserStatus.ACTIVE)
    assigned = Project(name="Source Assigned")
    hidden = Project(name="Source Hidden")
    db_session.add_all([staff, assigned, hidden])
    await db_session.flush()
    db_session.add(ProjectMember(project_id=assigned.id, user_id=staff.id))
    await db_session.commit()

    _login(api_client, "owner-code")
    owner_token = str(api_client.cookies.get("access_token"))
    api_client.cookies.clear()
    _login(api_client, "staff-code")
    staff_token = str(api_client.cookies.get("access_token"))

    api_client.cookies.clear()
    api_client.cookies.set("access_token", owner_token, domain="testserver.local", path="/")
    owner_cookie = api_client.get("/api/v1/projects", headers={"Authorization": f"Bearer {staff_token}"})
    assert {item["name"] for item in owner_cookie.json()} == {"Source Assigned", "Source Hidden"}

    api_client.cookies.clear()
    api_client.cookies.set("access_token", staff_token, domain="testserver.local", path="/")
    staff_cookie = api_client.get("/api/v1/projects", headers={"Authorization": f"Bearer {owner_token}"})
    assert [item["name"] for item in staff_cookie.json()] == ["Source Assigned"]

    api_client.cookies.set("access_token", "invalid", domain="testserver.local", path="/")
    invalid_cookie = api_client.get("/api/v1/projects", headers={"Authorization": f"Bearer {owner_token}"})
    assert invalid_cookie.status_code == 401

    api_client.cookies.clear()
    header_only = api_client.get("/api/v1/projects", headers={"Authorization": f"Bearer {owner_token}"})
    assert {item["name"] for item in header_only.json()} == {"Source Assigned", "Source Hidden"}


def test_project_errors_have_safe_correlatable_bodies(api_client: TestClient) -> None:
    """Returning framework exceptions would leak inconsistent, uncorrelatable error bodies."""
    response = api_client.get("/api/v1/projects")
    assert response.status_code == 401
    assert response.json()["error"] == {
        "code": "AUTHENTICATION_REQUIRED",
        "message": "Authentication required",
        "request_id": response.json()["error"]["request_id"],
    }
    assert response.json()["error"]["request_id"]


def test_unauthenticated_project_write_uses_401_error_contract(api_client: TestClient) -> None:
    """A CSRF-first response would incorrectly report an anonymous project write as 403."""
    response = api_client.post("/api/v1/projects", json={"name": "Anonymous"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"
    assert response.json()["error"]["request_id"]


def test_invalid_header_only_bearer_uses_safe_401_error_contract(api_client: TestClient) -> None:
    """Returning the legacy detail body would violate the project API error contract."""
    response = api_client.post(
        "/api/v1/projects", json={"name": "Invalid"}, headers={"Authorization": "Bearer invalid"}
    )
    assert response.status_code == 401
    assert response.json()["error"] == {
        "code": "AUTHENTICATION_REQUIRED",
        "message": "Authentication required",
        "request_id": response.json()["error"]["request_id"],
    }
    assert response.json()["error"]["request_id"]


def test_project_creation_requires_browser_csrf(api_client: TestClient) -> None:
    """Bypassing the existing browser-CSRF boundary enables cookie-authenticated writes."""
    _login(api_client, "owner-code")
    assert api_client.post("/api/v1/projects", json={"name": "No CSRF"}).status_code == 403


@pytest.mark.parametrize("request_id", ["trace.123_OK", "evil\r\nX-Injected: yes", "x" * 9000, "   ", "bad\x00id"])
def test_error_request_id_is_trusted_or_replaced(api_client: TestClient, request_id: str) -> None:
    """Reflecting hostile request IDs enables header/log injection and amplification."""
    response = api_client.get("/api/v1/projects", headers={"X-Request-ID": request_id})
    actual = response.json()["error"]["request_id"]
    assert actual == response.headers["X-Request-ID"]
    if request_id == "trace.123_OK":
        assert actual == request_id
    else:
        assert actual != request_id
        assert len(actual) <= 128


@pytest.mark.parametrize(
    ("name", "status"),
    [("x", 201), ("x" * 255, 201), ("x" * 256, 422), ("   ", 422)],
)
def test_project_name_is_canonical_and_bounded(
    api_client: TestClient, name: str, status: int
) -> None:
    """Raw-length validation permits empty or oversized persisted project names."""
    _login(api_client, "owner-code")
    response = api_client.post(
        "/api/v1/projects", json={"name": name}, headers=_csrf_headers(api_client)
    )
    assert response.status_code == status


def test_project_name_trims_and_rejects_case_insensitive_aliases(api_client: TestClient) -> None:
    """Raw unique names allow whitespace and case aliases for one project identity."""
    _login(api_client, "owner-code")
    first = api_client.post(
        "/api/v1/projects", json={"name": "  Core  "}, headers=_csrf_headers(api_client)
    )
    assert first.status_code == 201 and first.json()["name"] == "Core"
    duplicate = api_client.post(
        "/api/v1/projects", json={"name": "core"}, headers=_csrf_headers(api_client)
    )
    assert duplicate.status_code == 409


def test_is_test_requires_json_boolean(api_client: TestClient) -> None:
    """Coercing string false silently accepts a non-JSON-boolean API value."""
    _login(api_client, "owner-code")
    response = api_client.post(
        "/api/v1/projects", json={"name": "Strict", "is_test": "false"}, headers=_csrf_headers(api_client)
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_concurrent_canonical_name_creates_have_one_winner(
    db_session: AsyncSession, test_settings: Settings
) -> None:
    """Without the PostgreSQL functional unique index equivalent names can both commit."""
    owner = User(wecom_userid="owner-1", display_name="Owner", role=Role.OWNER, status=UserStatus.ACTIVE)
    db_session.add(owner)
    await db_session.flush()
    token = (
        await AuthService(
            db_session,
            AuthRepository(db_session),
            UserRepository(db_session),
            None,
            test_settings,
        ).issue_session(owner)
    ).access_token
    await db_session.commit()
    transport = httpx.ASGITransport(app=create_app(test_settings))

    async def create(name: str) -> int:
        async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as client:
            response = await client.post(
                "/api/v1/projects",
                json={"name": name},
                headers={"Authorization": f"Bearer {token}"},
            )
            return response.status_code

    for first, second in (("Race Core", " race core "), ("Race Next", "RACE NEXT")):
        assert sorted(await asyncio.gather(create(first), create(second))) == [201, 409]
    await db_session.execute(delete(Project))
    await db_session.execute(delete(AuthSession))
    await db_session.execute(delete(User))
    await db_session.commit()


def test_missing_project_and_invalid_creation_use_safe_error_mapping(api_client: TestClient) -> None:
    """Falling back to FastAPI defaults would break safe 404/422 API error contracts."""
    _login(api_client, "owner-code")
    missing = api_client.get(f"/api/v1/projects/{uuid4()}")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "PROJECT_NOT_FOUND"

    invalid = api_client.post("/api/v1/projects", json={"name": ""}, headers=_csrf_headers(api_client))
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "VALIDATION_ERROR"
    assert invalid.json()["error"]["request_id"]


@pytest.mark.asyncio
async def test_duplicate_project_name_is_conflict(
    api_client: TestClient, db_session: AsyncSession
) -> None:
    """Removing duplicate detection would permit ambiguous project identities."""
    db_session.add(Project(name="Unique"))
    await db_session.commit()
    _login(api_client, "owner-code")
    response = api_client.post(
        "/api/v1/projects", json={"name": "Unique"}, headers=_csrf_headers(api_client)
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PROJECT_NAME_CONFLICT"


@pytest.mark.asyncio
async def test_disabled_browser_session_is_rejected_immediately(
    api_client: TestClient, db_session: AsyncSession
) -> None:
    """Trusting JWT role/status claims would leave changed accounts authorized."""
    _login(api_client, "owner-code")
    owner = await db_session.scalar(select(User).where(User.wecom_userid == "owner-1"))
    assert owner is not None
    owner.status = UserStatus.DISABLED
    await db_session.commit()
    response = api_client.get("/api/v1/projects")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"


@pytest.mark.asyncio
async def test_role_changed_browser_session_uses_current_role_immediately(
    api_client: TestClient, db_session: AsyncSession
) -> None:
    """Trusting the role claim would allow a demoted OWNER to create a project."""
    _login(api_client, "owner-code")
    owner = await db_session.scalar(select(User).where(User.wecom_userid == "owner-1"))
    assert owner is not None
    owner.role = Role.STAFF
    await db_session.commit()
    response = api_client.post(
        "/api/v1/projects", json={"name": "Demoted"}, headers=_csrf_headers(api_client)
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"


@pytest.mark.asyncio
async def test_revoked_browser_session_is_rejected_immediately(
    api_client: TestClient, db_session: AsyncSession
) -> None:
    """Ignoring server-side revocation would keep a logged-out session authorized."""
    _login(api_client, "owner-code")
    auth_session = await db_session.scalar(select(AuthSession))
    assert auth_session is not None
    auth_session.revoked_at = auth_session.created_at
    await db_session.commit()
    response = api_client.get("/api/v1/projects")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"
