"""Audit behavior exposed through real project HTTP requests."""

import ast
import asyncio
import inspect
import re
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI, Request
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.routing import Mount

from superboss.core.actors import Actor
from superboss.core.config import Settings
from superboss.main import create_app
from superboss.modules.audit.models import AuditLog
from superboss.modules.audit.schemas import AuditEventInput
from superboss.modules.audit.service import AuditService
from superboss.modules.auth.models import AuthSession
from superboss.modules.auth.service import AuthService
from superboss.modules.files.models import File, FileState
from superboss.modules.projects.models import Project, ProjectMember
from superboss.modules.projects.router import get_service, get_session
from superboss.modules.projects.schemas import ProjectCreate
from superboss.modules.projects.service import ProjectService
from superboss.modules.users.models import Role, User
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
async def test_documented_audit_sql_returns_complete_owner_staff_device_evidence(
    client: TestClient, db_session: AsyncSession
) -> None:
    del client
    owner_id, staff_id, device_id, outsider_id = uuid4(), uuid4(), uuid4(), uuid4()
    foreign_file_id = uuid4()
    started_at = datetime.now(UTC) - timedelta(minutes=1)
    required = [
        ("user", owner_id, "auth.login", "SUCCESS", owner_id),
        ("user", owner_id, "project.create", "SUCCESS", uuid4()),
        ("user", owner_id, "file.upload.complete", "SUCCESS", uuid4()),
        ("user", owner_id, "file.download", "DENIED", uuid4()),
        ("user", owner_id, "file.download", "SUCCESS", uuid4()),
        ("user", owner_id, "device.pairing_code.create", "SUCCESS", None),
        ("user", owner_id, "device.revoke", "SUCCESS", device_id),
        ("user", owner_id, "import.list", "SUCCESS", None),
        ("user", staff_id, "auth.login", "SUCCESS", staff_id),
        ("user", staff_id, "project.create", "DENIED", None),
        ("user", staff_id, "device.pairing_code.create", "DENIED", None),
        ("user", staff_id, "import.list", "DENIED", None),
        ("user", staff_id, "file.download", "DENIED", foreign_file_id),
        ("device", device_id, "device.pair", "SUCCESS", device_id),
        ("device", device_id, "import.submit", "SUCCESS", uuid4()),
    ]
    db_session.add_all(
        [
            AuditLog(
                actor_kind=actor_kind,
                actor_id=actor_id,
                action=action,
                object_type="acceptance",
                object_id=object_id,
                outcome=outcome,
                metadata_json={"actor_role": "OWNER" if actor_id == owner_id else None},
                request_id=uuid4(),
            )
            for actor_kind, actor_id, action, outcome, object_id in required
        ]
        + [
            AuditLog(
                actor_kind="user",
                actor_id=outsider_id,
                action="auth.login",
                object_type="user",
                object_id=outsider_id,
                outcome="SUCCESS",
                metadata_json={"actor_role": "STAFF"},
                request_id=uuid4(),
            )
        ]
    )
    await db_session.commit()
    ended_at = datetime.now(UTC) + timedelta(minutes=1)

    runbook = (Path(__file__).resolve().parents[3] / "docs/runbooks/m1-owner-acceptance.md").read_text(
        encoding="utf-8"
    )
    match = re.search(r'\$AuditSql=@"\n(.*?)\n"@', runbook, re.DOTALL)
    assert match is not None
    documented_sql = match.group(1)
    assert documented_sql.startswith("\\set ON_ERROR_STOP on\n")
    substitutions = {
        "$OwnerActorId": str(owner_id),
        "$StaffActorId": str(staff_id),
        "$DeviceActorId": str(device_id),
        "$AuditStart": started_at.isoformat(),
        "$AuditEnd": ended_at.isoformat(),
    }
    for placeholder, value in substitutions.items():
        assert placeholder in documented_sql
        documented_sql = documented_sql.replace(placeholder, value)
    query = documented_sql.removeprefix("\\set ON_ERROR_STOP on\n")
    rows = (await db_session.execute(text(query))).mappings().all()

    assert {
        (row["actor_kind"], row["actor_id"], row["action"], row["outcome"], row["object_id"])
        for row in rows
    } == set(required)


@pytest.mark.asyncio
async def test_acceptance_query_boundary_returns_all_four_staff_denials(
    client: TestClient, db_session: AsyncSession
) -> None:
    staff = local_user("staff-1", display_name="Acceptance staff")
    foreign_project = Project(name="Acceptance foreign project")
    db_session.add_all([staff, foreign_project])
    await db_session.flush()
    foreign_file = File(
        project_id=foreign_project.id,
        filename="staff-foreign-probe.json",
        category="E2E",
        file_date=date(2026, 8, 11),
        object_key=f"projects/{foreign_project.id}/E2E/2026-08-11/clean/staff-probe.json",
        size_bytes=1,
        sha256="0" * 64,
        state=FileState.CLEAN,
        uploader_id=staff.id,
        uploader_kind="user",
        content_type="application/json",
    )
    db_session.add(foreign_file)
    await db_session.commit()
    _login(client, "staff-code")
    csrf = {"X-CSRF-Token": str(client.cookies.get("XSRF-TOKEN"))}
    started_at = datetime.now(UTC)
    request_ids = {
        action: uuid4()
        for action in (
            "project.create",
            "device.pairing_code.create",
            "import.list",
            "file.download",
        )
    }

    responses = [
        client.post(
            "/api/v1/projects",
            json={"name": "Forbidden acceptance project"},
            headers={**csrf, "X-Request-ID": str(request_ids["project.create"])},
        ),
        client.post(
            "/api/v1/owner/devices/pairing-codes",
            json={"project_ids": [str(foreign_project.id)]},
            headers={**csrf, "X-Request-ID": str(request_ids["device.pairing_code.create"])},
        ),
        client.get(
            "/api/v1/owner/import-jobs?limit=1",
            headers={"X-Request-ID": str(request_ids["import.list"])},
        ),
        client.get(
            f"/api/v1/files/{foreign_file.id}/download",
            headers={"X-Request-ID": str(request_ids["file.download"])},
        ),
    ]
    ended_at = datetime.now(UTC)
    assert [response.status_code for response in responses] == [403, 403, 403, 403]

    events = list(
        await db_session.scalars(
            select(AuditLog).where(
                AuditLog.actor_id == staff.id,
                AuditLog.created_at >= started_at,
                AuditLog.created_at <= ended_at,
                AuditLog.action.in_(tuple(request_ids)),
                AuditLog.outcome == "DENIED",
            )
        )
    )
    by_action = {event.action: event for event in events}
    assert set(by_action) == set(request_ids)
    assert all(by_action[action].request_id == request_ids[action] for action in request_ids)
    assert all(
        by_action[action].object_id is None
        for action in ("project.create", "device.pairing_code.create", "import.list")
    )
    assert by_action["file.download"].object_id == foreign_file.id


@pytest.mark.asyncio
async def test_records_denied_foreign_project_read_with_response_request_id(
    client: TestClient, db_session: AsyncSession
) -> None:
    """Dropping denied evidence would hide staff attempts to access other projects."""
    staff = local_user("staff-1", display_name="Staff")
    foreign_project = Project(name="Foreign")
    db_session.add_all([staff, foreign_project])
    await db_session.commit()
    _login(client, "staff-code")

    response = client.get(f"/api/v1/projects/{foreign_project.id}")

    assert response.status_code == 403
    events = list(
        (
            await db_session.scalars(
                select(AuditLog).where(AuditLog.action == "project.read")
            )
        ).all()
    )
    assert len(events) == 1
    event = events[0]
    assert event.action == "project.read"
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


@pytest.mark.parametrize(
    ("method", "path", "status"),
    [
        ("post", "/api/v1/projects/not/a/route", 404),
        ("put", "/api/v1/projects/not/a/route", 404),
        ("patch", "/api/v1/projects/not/a/route", 404),
        ("delete", "/api/v1/projects/not/a/route", 404),
        ("post", "/api/v1/projects/not-a-uuid", 405),
    ],
)
def test_unsafe_unmatched_project_responses_always_have_request_id(
    client: TestClient, method: str, path: str, status: int
) -> None:
    """The anonymous project-write exception must not bypass correlation headers."""
    response = getattr(client, method)(path)
    assert response.status_code == status
    assert UUID(response.headers["X-Request-ID"])


@pytest.mark.asyncio
async def test_non_project_and_unmatched_requests_have_ids_but_no_audit(
    client: TestClient, db_session: AsyncSession
) -> None:
    """Infrastructure and framework traffic is correlatable without polluting audit history."""
    before = await db_session.scalar(select(AuditLog))
    assert before is None
    assert not any(
        isinstance(route, Mount) and route.name == "static" for route in client.app.routes
    )
    for method, path in [
        ("get", "/api/v1/health/live"),
        ("get", "/favicon.ico"),
        ("get", "/static/app.css"),
        ("get", "/missing"),
    ]:
        response = getattr(client, method)(path)
        assert UUID(response.headers["X-Request-ID"])
    assert await db_session.scalar(select(AuditLog)) is None


class _CommitFailure:
    async def commit(self) -> None:
        raise RuntimeError("controlled commit failure")


@pytest.mark.asyncio
async def test_business_commit_failure_persists_no_success_audit(
    db_session: AsyncSession,
) -> None:
    """A failed business commit must stop before durable success evidence is written."""
    request_id = UUID("33f7445d-b60f-43e7-9d86-5da70d3d705d")
    actor = Actor("user", uuid4(), Role.OWNER, frozenset(), frozenset())
    assert db_session.bind is not None
    service = ProjectService(
        db_session,
        AuditService(async_sessionmaker(db_session.bind, expire_on_commit=False)),
    )
    project = await service.create(actor, ProjectCreate(name="Commit failure"), request_id)
    service.session = _CommitFailure()  # type: ignore[assignment]
    with pytest.raises(RuntimeError, match="controlled commit failure"):
        await service.commit_and_record_success(actor, "project.create", request_id, project.id)
    assert not list(
        (await db_session.scalars(select(AuditLog).where(AuditLog.request_id == request_id))).all()
    )


@pytest.mark.asyncio
async def test_successful_commit_control_persists_one_success_audit(
    db_session: AsyncSession,
) -> None:
    """The failure test's control proves a real committed unit of work does record success."""
    request_id = UUID("aa5e21e9-051b-4e7c-97ba-04421e9d202c")
    actor = Actor("user", uuid4(), Role.OWNER, frozenset(), frozenset())
    assert db_session.bind is not None
    service = ProjectService(
        db_session,
        AuditService(async_sessionmaker(db_session.bind, expire_on_commit=False)),
    )
    project = await service.create(actor, ProjectCreate(name="Commit success"), request_id)
    await service.commit_and_record_success(actor, "project.create", request_id, project.id)
    events = list(
        (await db_session.scalars(select(AuditLog).where(AuditLog.request_id == request_id))).all()
    )
    assert len(events) == 1 and events[0].outcome == "SUCCESS"


class _FailingAuditService:
    async def record(self, event: AuditEventInput) -> UUID:
        del event
        raise RuntimeError("audit secret must never escape")


@pytest.mark.asyncio
async def test_audit_write_failure_is_not_returned_as_project_success(
    db_session: AsyncSession, test_settings: Settings
) -> None:
    """After the business commit, a failed audit write must surface as a safe non-success response."""

    async def failing_service(
        request: Request, session: AsyncSession = Depends(get_session)
    ) -> ProjectService:
        del request
        return ProjectService(session, _FailingAuditService())  # type: ignore[arg-type]

    db_session.add(local_user("owner", display_name="Owner", role=Role.OWNER))
    await db_session.commit()
    app = create_app(test_settings)
    app.dependency_overrides[get_service] = failing_service
    try:
        with TestClient(
            app, base_url="https://testserver", raise_server_exceptions=False
        ) as fault_client:
            _login(fault_client, "owner-code")
            request_id = UUID("e918ab2f-8b4d-4164-a7b2-246119dc1b21")
            response = fault_client.post(
                "/api/v1/projects",
                json={"name": "Committed despite audit fault"},
                headers={
                    "X-CSRF-Token": str(fault_client.cookies.get("XSRF-TOKEN")),
                    "X-Request-ID": str(request_id),
                },
            )
        assert not 200 <= response.status_code < 300
        assert response.headers["X-Request-ID"] == str(request_id)
        assert (
            "audit secret" not in response.text.lower() and "internal" not in response.text.lower()
        )
        assert (
            await db_session.scalar(
                select(Project).where(Project.name == "Committed despite audit fault")
            )
            is not None
        )
        assert not list(
            (
                await db_session.scalars(select(AuditLog).where(AuditLog.request_id == request_id))
            ).all()
        )
    finally:
        app.dependency_overrides.pop(get_service, None)


@pytest.mark.asyncio
async def test_concurrent_request_ids_keep_audit_actors_isolated(
    db_session: AsyncSession, test_settings: Settings
) -> None:
    """Concurrent clients must never exchange request IDs or actor identities in durable audit rows."""
    owner = local_user("owner", display_name="Owner", role=Role.OWNER)
    staff = local_user("staff-1", display_name="Staff")
    db_session.add_all([owner, staff, Project(name="Assigned")])
    await db_session.flush()
    assigned = await db_session.scalar(select(Project).where(Project.name == "Assigned"))
    assert assigned is not None
    db_session.add(ProjectMember(project_id=assigned.id, user_id=staff.id))
    service = AuthService(db_session, test_settings)
    tokens: list[tuple[UUID, str, UUID, str]] = []
    for index in range(10):
        user = owner if index % 2 == 0 else staff
        pair = await service.issue_session(user)
        tokens.append(
            (
                UUID(f"00000000-0000-4000-8000-{index + 1:012d}"),
                pair.access_token,
                user.id,
                user.role.value,
            )
        )
    await db_session.commit()
    app = create_app(test_settings)
    ready = 0
    ready_lock = asyncio.Lock()
    start = asyncio.Event()

    async def request_one(
        request_id: UUID, token: str, actor_id: UUID, role: str
    ) -> tuple[UUID, UUID, str, httpx.Response]:
        nonlocal ready
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://testserver",
            cookies={"access_token": token},
        ) as async_client:
            async with ready_lock:
                ready += 1
                if ready == len(tokens):
                    start.set()
            await start.wait()
            response = await async_client.get(
                "/api/v1/projects", headers={"X-Request-ID": str(request_id)}
            )
            return request_id, actor_id, role, response

    try:
        results = await asyncio.gather(*(request_one(*item) for item in tokens))
        for request_id, actor_id, role, response in results:
            assert response.status_code == 200
            assert response.headers["X-Request-ID"] == str(request_id)
            events = list(
                (
                    await db_session.scalars(
                        select(AuditLog).where(AuditLog.request_id == request_id)
                    )
                ).all()
            )
            assert len(events) == 1
            event = events[0]
            assert (event.actor_kind, event.actor_id, event.action, event.outcome) == (
                "user",
                actor_id,
                "project.list",
                "SUCCESS",
            )
            assert event.object_id is None and event.project_id is None
            assert event.metadata_json["actor_role"] == role
    finally:
        await db_session.rollback()
        await db_session.execute(delete(AuditLog))
        await db_session.execute(delete(ProjectMember))
        await db_session.execute(delete(Project))
        await db_session.execute(delete(AuthSession))
        await db_session.execute(delete(User))
        await db_session.commit()
        assert await db_session.scalar(select(AuditLog)) is None
        assert await db_session.scalar(select(Project)) is None
        assert await db_session.scalar(select(User)) is None


_UNSAFE_ALLOWLIST = {
    ("POST", "/api/v1/auth/login", "superboss.modules.auth.router", "login"),
    (
        "POST",
        "/api/v1/auth/password/change",
        "superboss.modules.auth.router",
        "change_password",
    ),
    ("POST", "/api/v1/auth/refresh", "superboss.modules.auth.router", "refresh"),
    ("POST", "/api/v1/auth/logout", "superboss.modules.auth.router", "logout"),
    (
        "POST",
        "/api/v1/device-auth/pair",
        "superboss.modules.devices.router",
        "pair_device",
    ),
    (
        "POST",
        "/api/v1/device-auth/refresh",
        "superboss.modules.devices.router",
        "refresh_device",
    ),
    ("POST", "/api/v1/files/uploads", "superboss.modules.files.router", "start"),
    (
        "POST",
        "/api/v1/files/uploads/{upload_id}/complete",
        "superboss.modules.files.router",
        "complete",
    ),
    (
        "POST",
        "/api/v1/files/uploads/{upload_id}/parts/{part_number}",
        "superboss.modules.files.router",
        "part",
    ),
    (
        "POST",
        "/api/v1/owner/devices/pairing-codes",
        "superboss.modules.devices.router",
        "create_pairing_code",
    ),
    (
        "POST",
        "/api/v1/owner/users",
        "superboss.modules.users.router",
        "create_staff",
    ),
    (
        "PATCH",
        "/api/v1/owner/users/{user_id}",
        "superboss.modules.users.router",
        "update_staff",
    ),
    (
        "PUT",
        "/api/v1/owner/users/{user_id}/projects",
        "superboss.modules.users.router",
        "replace_projects",
    ),
    (
        "POST",
        "/api/v1/owner/users/{user_id}/password-reset",
        "superboss.modules.users.router",
        "reset_staff_password",
    ),
    (
        "POST",
        "/api/v1/device/import-jobs",
        "superboss.modules.imports.router",
        "create_import",
    ),
    (
        "POST",
        "/api/v1/device/import-jobs/{job_id}/attachments/{attachment_id}/complete",
        "superboss.modules.imports.router",
        "complete_import_attachment",
    ),
    (
        "POST",
        "/api/v1/device/import-jobs/{job_id}/attachments/{attachment_id}/parts/{part_number}",
        "superboss.modules.imports.router",
        "presign_import_part",
    ),
    (
        "POST",
        "/api/v1/device/import-jobs/{job_id}/submit",
        "superboss.modules.imports.router",
        "submit_import",
    ),
    (
        "DELETE",
        "/api/v1/owner/devices/{device_id}",
        "superboss.modules.devices.router",
        "revoke_device",
    ),
    ("POST", "/api/v1/projects", "superboss.modules.projects.router", "create_project"),
}


def _unsafe_routes(app: FastAPI) -> set[tuple[str, str, str, str]]:
    found: set[tuple[str, str, str, str]] = set()

    def visit(routes: object, prefix: str = "") -> None:
        for route in routes:  # type: ignore[union-attr]
            if isinstance(route, APIRoute):
                for method in route.methods & {"POST", "PUT", "PATCH", "DELETE"}:
                    found.add(
                        (
                            method,
                            prefix + route.path,
                            route.endpoint.__module__,
                            route.endpoint.__name__,
                        )
                    )
            elif hasattr(route, "original_router"):
                context = route.include_context
                visit(route.original_router.routes, prefix + context.prefix)

    visit(app.routes)
    return found


def _assert_audit_mutation_free(source: str) -> None:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            name = node.name.lower()
            assert not (
                name.startswith(("delete", "update", "remove", "purge"))
                and any(word in name for word in ("audit", "event", "log"))
            )
        if isinstance(node, ast.Call):
            rendered = ast.unparse(node)
            assert "delete(AuditLog" not in rendered and "update(AuditLog" not in rendered
            assert (
                "DELETE FROM AUDIT_LOGS" not in rendered.upper()
                and "UPDATE AUDIT_LOGS" not in rendered.upper()
            )


@pytest.mark.asyncio
async def test_audit_is_append_only_at_route_and_source_boundaries(
    db_session: AsyncSession, test_settings: Settings
) -> None:
    """No unsafe route or audit implementation may update/delete a durable event."""
    seed = AuditLog(
        actor_kind="system",
        actor_id=None,
        action="seed.action",
        object_type="seed",
        object_id=None,
        project_id=None,
        outcome="SUCCESS",
        metadata_json={"fixed": "value"},
        request_id=UUID("99e3d94e-9c94-44df-86b1-2399aa63bbd0"),
    )
    db_session.add(seed)
    db_session.add(local_user("owner", display_name="Owner", role=Role.OWNER))
    await db_session.commit()
    seed_id = seed.id
    expected = (
        seed.actor_kind,
        seed.action,
        seed.object_type,
        seed.outcome,
        seed.metadata_json,
        seed.request_id,
    )
    app = create_app(test_settings)
    try:
        assert _unsafe_routes(app) == _UNSAFE_ALLOWLIST
        for route in app.routes:
            if isinstance(route, APIRoute) and route.methods & {"POST", "PUT", "PATCH", "DELETE"}:
                parameter_text = str(inspect.signature(route.endpoint))
                assert "AuditLog" not in parameter_text and "AuditService" not in parameter_text
        with TestClient(app, base_url="https://testserver") as test_client:
            _login(test_client, "owner-code")
            csrf = {"X-CSRF-Token": str(test_client.cookies.get("XSRF-TOKEN"))}
            assert test_client.post("/api/v1/auth/refresh", headers=csrf).status_code == 204
            csrf = {"X-CSRF-Token": str(test_client.cookies.get("XSRF-TOKEN"))}
            assert test_client.post("/api/v1/auth/logout", headers=csrf).status_code == 204
            _login(test_client, "owner-code")
            assert (
                test_client.post(
                    "/api/v1/projects",
                    json={"name": "Append route"},
                    headers={"X-CSRF-Token": str(test_client.cookies.get("XSRF-TOKEN"))},
                ).status_code
                == 201
            )
            csrf = {"X-CSRF-Token": str(test_client.cookies.get("XSRF-TOKEN"))}
            for method, path in [
                ("post", "/api/v1/audit"),
                ("put", "/api/v1/events/1"),
                ("delete", "/api/v1/logs/1"),
            ]:
                response = getattr(test_client, method)(path, headers=csrf)
                assert response.status_code in {404, 405}
        db_session.expire_all()
        unchanged = await db_session.get(AuditLog, seed_id)
        assert unchanged is not None
        assert (
            unchanged.actor_kind,
            unchanged.action,
            unchanged.object_type,
            unchanged.outcome,
            unchanged.metadata_json,
            unchanged.request_id,
        ) == expected
        public_methods = {
            name
            for name, value in inspect.getmembers(AuditService, inspect.isfunction)
            if not name.startswith("_")
        }
        assert public_methods == {"record"}
        source_root = Path(__file__).resolve().parents[2] / "src" / "superboss"
        for source in source_root.rglob("*.py"):
            _assert_audit_mutation_free(source.read_text(encoding="utf-8"))
    finally:
        await db_session.rollback()
        await db_session.execute(delete(AuditLog))
        await db_session.commit()
        assert await db_session.scalar(select(AuditLog)) is None


def test_audit_guard_rejects_synthetic_mutation_variants() -> None:
    """The source and route guards must fail on realistic future audit mutations."""
    for source in (
        "from sqlalchemy import delete\ndef delete_event():\n delete(AuditLog)",
        "from sqlalchemy import text\ndef run():\n text('DELETE FROM audit_logs')",
        "def update_audit_log():\n pass",
    ):
        with pytest.raises(AssertionError):
            _assert_audit_mutation_free(source)
    _assert_audit_mutation_free("def record():\n session.add(AuditLog())")
    with pytest.raises(AssertionError):
        assert _UNSAFE_ALLOWLIST == _UNSAFE_ALLOWLIST | {("DELETE", "/events", "x", "x")}
