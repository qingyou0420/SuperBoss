"""Audit behavior exposed through real project HTTP requests."""

from collections.abc import AsyncIterator
from uuid import UUID

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from superboss.core.config import Settings
from superboss.main import create_app
from superboss.modules.audit.models import AuditLog
from superboss.modules.auth.models import AuthSession, OAuthState
from superboss.modules.projects.models import Project, ProjectMember
from superboss.modules.users.models import Role, User, UserStatus


@pytest_asyncio.fixture
async def client(db_session: AsyncSession, test_settings: Settings) -> AsyncIterator[TestClient]:
    app = create_app(test_settings)
    with TestClient(app, base_url="https://testserver") as test_client:
        yield test_client
    await db_session.rollback()
    await db_session.execute(delete(AuditLog))
    await db_session.execute(delete(ProjectMember))
    await db_session.execute(delete(Project))
    await db_session.execute(delete(OAuthState))
    await db_session.execute(delete(AuthSession))
    await db_session.execute(delete(User))
    await db_session.commit()


def _login(client: TestClient, code: str) -> None:
    started = client.get("/api/v1/auth/wecom/start")
    response = client.get("/api/v1/auth/wecom/callback", params={"code": code, "state": started.json()["state"]})
    assert response.status_code == 204


async def _latest_event(session: AsyncSession) -> AuditLog:
    event = await session.scalar(select(AuditLog).order_by(AuditLog.created_at.desc()))
    assert event is not None
    return event


@pytest.mark.asyncio
async def test_records_denied_foreign_project_read_with_response_request_id(
    client: TestClient, db_session: AsyncSession
) -> None:
    """Dropping denied evidence would hide staff attempts to access other projects."""
    staff = User(wecom_userid="staff-1", display_name="Staff", role=Role.STAFF, status=UserStatus.ACTIVE)
    foreign_project = Project(name="Foreign")
    db_session.add_all([staff, foreign_project])
    await db_session.commit()
    _login(client, "staff-code")

    response = client.get(f"/api/v1/projects/{foreign_project.id}")

    assert response.status_code == 403
    event = await _latest_event(db_session)
    assert event.action == "project.read"
    assert event.outcome == "DENIED"
    assert event.project_id == foreign_project.id
    assert event.object_type == "project"
    assert event.object_id == foreign_project.id
    assert event.actor_kind == "user"
    assert event.actor_id == staff.id
    assert event.request_id == UUID(response.headers["X-Request-ID"])


@pytest.mark.asyncio
async def test_records_successful_project_create_list_and_read(
    client: TestClient, db_session: AsyncSession
) -> None:
    """Omitting successful operations makes the audit history one-sided and incomplete."""
    _login(client, "owner-code")
    csrf = {"X-CSRF-Token": str(client.cookies.get("XSRF-TOKEN"))}
    created = client.post("/api/v1/projects", json={"name": "Audited"}, headers=csrf)
    assert created.status_code == 201
    project_id = UUID(created.json()["id"])
    listed = client.get("/api/v1/projects")
    read = client.get(f"/api/v1/projects/{project_id}")
    assert listed.status_code == 200
    assert read.status_code == 200

    events = list((await db_session.scalars(select(AuditLog))).all())
    by_action = {event.action: event for event in events}
    assert set(by_action) == {"project.create", "project.list", "project.read"}
    assert all(event.outcome == "SUCCESS" for event in by_action.values())
    assert by_action["project.create"].project_id == project_id
    assert by_action["project.read"].request_id == UUID(read.headers["X-Request-ID"])
    assert by_action["project.list"].metadata_json["actor_role"] == "OWNER"


def test_audit_log_has_no_http_mutation_routes(client: TestClient) -> None:
    """An exposed audit mutation endpoint would violate append-only event history."""
    paths = [getattr(route, "path", "") for route in client.app.routes]
    assert all("audit" not in path for path in paths)
