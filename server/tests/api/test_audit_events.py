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
from superboss.modules.auth.models import AuthSession
from superboss.modules.projects.models import Project, ProjectMember
from superboss.modules.users.models import User
from tests.identity import LOCAL_TEST_PASSWORD, local_user


@pytest_asyncio.fixture
async def client(
    db_session: AsyncSession, test_settings: Settings, active_owner: User
) -> AsyncIterator[TestClient]:
    del active_owner
    await db_session.commit()
    app = create_app(test_settings)
    with TestClient(app, base_url="https://testserver") as test_client:
        yield test_client
    await db_session.rollback()
    await db_session.execute(delete(AuditLog))
    await db_session.execute(delete(ProjectMember))
    await db_session.execute(delete(Project))
    await db_session.execute(delete(AuthSession))
    await db_session.execute(delete(User))
    await db_session.commit()


def _login(client: TestClient, code: str) -> None:
    username = {"owner-code": "owner", "staff-code": "staff-1"}.get(code, code)
    assert client.get("/api/v1/auth/csrf").status_code == 204
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": LOCAL_TEST_PASSWORD},
        headers={"X-CSRF-Token": str(client.cookies.get("XSRF-TOKEN"))},
    )
    assert response.status_code == 204


async def _latest_event(session: AsyncSession) -> AuditLog:
    event = await session.scalar(select(AuditLog).order_by(AuditLog.created_at.desc()))
    assert event is not None
    return event


@pytest.mark.asyncio
async def test_records_denied_staff_project_update_with_response_request_id(
    client: TestClient, db_session: AsyncSession
) -> None:
    """Dropping denied evidence would hide staff attempts to change projects."""
    staff = local_user("staff-1", display_name="Staff")
    foreign_project = Project(name="Foreign")
    db_session.add_all([staff, foreign_project])
    await db_session.commit()
    _login(client, "staff-code")

    response = client.patch(
        f"/api/v1/projects/{foreign_project.id}",
        json={"name": "Nope"},
        headers={"X-CSRF-Token": str(client.cookies.get("XSRF-TOKEN"))},
    )

    assert response.status_code == 403
    events = list(
        (
            await db_session.scalars(
                select(AuditLog).where(AuditLog.action == "project.update")
            )
        ).all()
    )
    assert len(events) == 1
    event = events[0]
    assert event.action == "project.update"
    assert event.outcome == "DENIED"
    assert event.project_id == foreign_project.id
    assert event.object_type == "project"
    assert event.object_id == foreign_project.id
    assert event.actor_kind == "user"
    assert event.actor_id == staff.id
    assert event.request_id == UUID(response.headers["X-Request-ID"])


@pytest.mark.asyncio
async def test_staff_create_denial_is_durable_once_with_complete_context(
    client: TestClient, db_session: AsyncSession
) -> None:
    """A rolled-back staff create attempt must still leave exactly one accountable denial event."""
    staff = local_user("staff-1", display_name="Staff")
    db_session.add(staff)
    await db_session.commit()
    _login(client, "staff-code")
    request_id = UUID("a0b56e3a-33c8-4bd3-a955-47bdb5f36c53")
    response = client.post(
        "/api/v1/projects",
        json={"name": "Forbidden create"},
        headers={
            "X-CSRF-Token": str(client.cookies.get("XSRF-TOKEN")),
            "X-Request-ID": str(request_id),
        },
    )
    assert response.status_code == 403
    events = list(
        (await db_session.scalars(select(AuditLog).where(AuditLog.request_id == request_id))).all()
    )
    assert len(events) == 1
    event = events[0]
    assert (event.action, event.outcome, event.actor_kind, event.actor_id) == (
        "project.create",
        "DENIED",
        "user",
        staff.id,
    )
    assert event.object_type == "project" and event.object_id is None and event.project_id is None
    assert event.request_id == UUID(response.headers["X-Request-ID"])
    assert (
        await db_session.scalar(select(Project).where(Project.name == "Forbidden create")) is None
    )


@pytest.mark.asyncio
async def test_failed_project_requests_do_not_create_success_audits(
    client: TestClient, db_session: AsyncSession
) -> None:
    """A conflict, missing target, or validation failure must not masquerade as success in history."""
    db_session.add(Project(name="Existing"))
    await db_session.commit()
    _login(client, "owner-code")
    csrf = {"X-CSRF-Token": str(client.cookies.get("XSRF-TOKEN"))}
    cases = [
        (
            "post",
            "/api/v1/projects",
            {**csrf, "X-Request-ID": "b3e4cc05-08ae-4f72-a1c6-49b0a00d42df"},
            {"name": "Existing"},
            409,
        ),
        (
            "get",
            f"/api/v1/projects/{UUID('c1c0f7ae-7169-4850-a2bf-779fe955fdd1')}",
            {"X-Request-ID": "7541a873-1c3e-4a9d-bec5-b95045042f45"},
            None,
            404,
        ),
        (
            "post",
            "/api/v1/projects",
            {**csrf, "X-Request-ID": "6ee23ac7-cbf5-4c98-88c6-59cbc7f36809"},
            {"name": ""},
            422,
        ),
    ]
    for method, path, headers, body, status in cases:
        response = (
            getattr(client, method)(path, headers=headers, json=body)
            if body is not None
            else getattr(client, method)(path, headers=headers)
        )
        assert response.status_code == status
        request_id = UUID(headers["X-Request-ID"])
        assert not list(
            (
                await db_session.scalars(select(AuditLog).where(AuditLog.request_id == request_id))
            ).all()
        )


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

    events = list(
        (
            await db_session.scalars(
                select(AuditLog).where(AuditLog.action.like("project.%"))
            )
        ).all()
    )
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
