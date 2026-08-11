"""HTTP boundaries for least-privilege Kimi device credentials."""

import json
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from superboss.core.config import Settings
from superboss.core.security import hash_token
from superboss.main import create_app
from superboss.modules.audit.models import AuditLog
from superboss.modules.devices.models import (
    DeviceConnection,
    DevicePairingCode,
    DeviceSession,
)
from superboss.modules.devices.service import DeviceService
from superboss.modules.files.models import File, FileState
from superboss.modules.projects.models import Project, ProjectStatus
from superboss.modules.users.models import User
from tests.files.storage import InMemoryObjectStorage
from tests.identity import LOCAL_TEST_PASSWORD, local_user


@pytest_asyncio.fixture
async def device_client(
    db_session: AsyncSession, test_settings: Settings, active_owner: User
) -> AsyncIterator[TestClient]:
    del active_owner
    await db_session.commit()
    app = create_app(test_settings, object_storage=InMemoryObjectStorage())
    with TestClient(
        app, base_url="https://testserver", raise_server_exceptions=False
    ) as client:
        yield client


def _login(client: TestClient, code: str = "owner-code") -> None:
    username = {"owner-code": "owner", "staff-code": "staff-1"}.get(code, code)
    assert client.get("/api/v1/auth/csrf").status_code == 204
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": LOCAL_TEST_PASSWORD},
        headers=_csrf(client),
    )
    assert response.status_code == 204


def _csrf(client: TestClient) -> dict[str, str]:
    return {"X-CSRF-Token": str(client.cookies.get("XSRF-TOKEN"))}


def _assert_error(response: httpx.Response, status: int, code: str) -> None:
    assert response.status_code == status
    body = response.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "request_id"}
    assert body["error"]["code"] == code
    assert body["error"]["request_id"] == response.headers["X-Request-ID"]
    assert "detail" not in body and "trace" not in str(body).lower()


def _test_factory(db_session: AsyncSession) -> async_sessionmaker[AsyncSession]:
    assert db_session.bind is not None
    return async_sessionmaker(db_session.bind, expire_on_commit=False)


async def _device_service(client: TestClient, db_session: AsyncSession) -> DeviceService:
    return DeviceService(_test_factory(db_session), client.app.state.settings)


async def _pair_device(
    client: TestClient,
    db_session: AsyncSession,
    owner_id: UUID,
    project_id: UUID,
    *,
    name: str = "Owner-PC",
) -> object:
    service = await _device_service(client, db_session)
    code = await service.create_pairing_code(owner_id, [project_id], request_id=uuid4())
    return await service.pair(code.raw_code, name, request_id=uuid4())


@pytest.mark.asyncio
async def test_exact_six_endpoints_return_bounded_non_secret_shapes(
    device_client: TestClient, db_session: AsyncSession, active_owner: User
) -> None:
    """Adding credentials, hashes, or session internals to responses expands the secret surface."""
    first = Project(name="Device HTTP A")
    second = Project(name="Device HTTP B")
    db_session.add_all([first, second])
    await db_session.commit()
    _login(device_client)

    code_response = device_client.post(
        "/api/v1/owner/devices/pairing-codes",
        json={"project_ids": [str(first.id), str(second.id)]},
        headers=_csrf(device_client),
    )
    assert code_response.status_code == 201
    assert set(code_response.json()) == {"raw_code", "expires_at"}
    raw_code = code_response.json()["raw_code"]
    assert len(raw_code) >= 64

    device_client.cookies.clear()
    pair_response = device_client.post(
        "/api/v1/device-auth/pair",
        json={"pairing_code": raw_code, "device_name": "  Owner-PC  "},
    )
    assert pair_response.status_code == 200
    assert set(pair_response.json()) == {
        "access_token",
        "refresh_token",
        "token_type",
        "expires_at",
        "refresh_expires_at",
    }
    assert "set-cookie" not in pair_response.headers
    first_access = pair_response.json()["access_token"]
    first_refresh = pair_response.json()["refresh_token"]

    refresh_response = device_client.post(
        "/api/v1/device-auth/refresh", json={"refresh_token": first_refresh}
    )
    assert refresh_response.status_code == 200
    assert set(refresh_response.json()) == set(pair_response.json())
    assert refresh_response.json()["access_token"] != first_access
    assert refresh_response.json()["refresh_token"] != first_refresh
    assert "set-cookie" not in refresh_response.headers
    access_token = refresh_response.json()["access_token"]

    me_response = device_client.get(
        "/api/v1/device-auth/me",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert me_response.status_code == 200
    assert set(me_response.json()) == {
        "id",
        "name",
        "scopes",
        "projects",
        "paired_at",
        "last_used_at",
    }
    assert me_response.json()["name"] == "Owner-PC"
    assert me_response.json()["scopes"] == [
        "imports:create",
        "imports:read-own",
        "imports:submit",
        "imports:upload",
    ]
    assert {(item["id"], item["name"]) for item in me_response.json()["projects"]} == {
        (str(first.id), first.name),
        (str(second.id), second.name),
    }
    assert all(set(item) == {"id", "name"} for item in me_response.json()["projects"])

    _login(device_client)
    listed = device_client.get("/api/v1/owner/devices")
    assert listed.status_code == 200 and len(listed.json()) == 1
    device = listed.json()[0]
    assert set(device) == {
        "id",
        "name",
        "paired_at",
        "last_used_at",
        "revoked_at",
        "status",
        "projects",
    }
    assert device["status"] == "ACTIVE"
    serialized = str([code_response.json(), pair_response.json(), me_response.json(), device])
    async with _test_factory(db_session)() as session:
        pairing = await session.scalar(select(DevicePairingCode))
        refresh = await session.scalar(
            select(DeviceSession).order_by(DeviceSession.created_at.desc())
        )
    assert pairing is not None and refresh is not None
    assert pairing.code_hash not in serialized
    assert refresh.refresh_token_hash not in serialized


@pytest.mark.asyncio
async def test_owner_endpoint_actor_matrix_and_browser_csrf(
    device_client: TestClient, db_session: AsyncSession, active_owner: User
) -> None:
    """OWNER management must reject anonymous, STAFF, and device actors before mutation."""
    project = Project(name="Owner matrix")
    staff = local_user("staff-1", display_name="Staff")
    db_session.add_all([project, staff])
    await db_session.commit()
    body = {"project_ids": [str(project.id)]}

    _assert_error(
        device_client.post("/api/v1/owner/devices/pairing-codes", json=body),
        401,
        "AUTHENTICATION_REQUIRED",
    )
    _login(device_client, "staff-code")
    _assert_error(
        device_client.post(
            "/api/v1/owner/devices/pairing-codes",
            json=body,
            headers=_csrf(device_client),
        ),
        403,
        "OWNER_REQUIRED",
    )
    device_client.cookies.clear()
    pair = await _pair_device(device_client, db_session, active_owner.id, project.id)
    _assert_error(
        device_client.post(
            "/api/v1/owner/devices/pairing-codes",
            json=body,
            headers={"Authorization": f"Bearer {pair.access_token}"},
        ),
        403,
        "OWNER_REQUIRED",
    )
    _assert_error(
        device_client.get(
            "/api/v1/owner/devices",
            headers={"Authorization": f"Bearer {pair.access_token}"},
        ),
        403,
        "OWNER_REQUIRED",
    )

    device_client.cookies.clear()
    _login(device_client)
    _assert_error(
        device_client.post("/api/v1/owner/devices/pairing-codes", json=body),
        403,
        "CSRF_VALIDATION_FAILED",
    )


@pytest.mark.asyncio
async def test_device_and_browser_credentials_never_substitute_for_each_other(
    device_client: TestClient, db_session: AsyncSession, active_owner: User
) -> None:
    """Falling through between JWT shapes would let a device browse or a browser impersonate it."""
    project = Project(name="Credential boundary")
    db_session.add(project)
    await db_session.flush()
    file = File(
        project_id=project.id,
        filename="device-private.pdf",
        category="docs",
        file_date=date(2026, 8, 9),
        object_key=f"projects/{project.id}/docs/device-private.pdf",
        size_bytes=1,
        sha256="0" * 64,
        state=FileState.CLEAN,
        uploader_kind="user",
        uploader_id=active_owner.id,
        content_type="application/pdf",
    )
    db_session.add(file)
    await db_session.commit()
    pair = await _pair_device(device_client, db_session, active_owner.id, project.id)
    device_headers = {"Authorization": f"Bearer {pair.access_token}"}

    _assert_error(device_client.get("/api/v1/projects", headers=device_headers), 403, "PROJECT_FORBIDDEN")
    download = device_client.get(
        f"/api/v1/files/{file.id}/download", headers=device_headers
    )
    _assert_error(
        download,
        403,
        "PROJECT_FORBIDDEN",
    )
    storage = device_client.app.state.object_storage
    assert storage.expiries == []
    denied = await db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "file.download",
            AuditLog.object_id == file.id,
            AuditLog.outcome == "DENIED",
        )
    )
    assert denied is not None and denied.actor_kind == "device"
    assert pair.access_token not in str(denied.metadata_json)
    assert "authorization" not in str(denied.metadata_json).lower()
    _assert_error(
        device_client.get("/api/v1/owner/devices", headers=device_headers),
        403,
        "OWNER_REQUIRED",
    )

    _login(device_client)
    _assert_error(
        device_client.get("/api/v1/device-auth/me"), 401, "AUTHENTICATION_REQUIRED"
    )
    _assert_error(
        device_client.get("/api/v1/device-auth/me", headers=device_headers),
        401,
        "AUTHENTICATION_REQUIRED",
    )


def test_only_exact_pair_and_refresh_paths_are_exempt_from_csrf(
    device_client: TestClient,
) -> None:
    """A prefix exemption would silently exempt future state-changing device routes."""
    pair = device_client.post(
        "/api/v1/device-auth/pair",
        json={"pairing_code": "invalid", "device_name": "Owner-PC"},
    )
    refresh = device_client.post(
        "/api/v1/device-auth/refresh", json={"refresh_token": "invalid"}
    )
    _assert_error(pair, 401, "AUTHENTICATION_REQUIRED")
    _assert_error(refresh, 401, "AUTHENTICATION_REQUIRED")
    for path in (
        "/api/v1/device-auth/pair/extra",
        "/api/v1/device-auth/me",
        "/api/v1/device-auth/anything",
    ):
        _assert_error(
            device_client.post(path, json={}), 403, "CSRF_VALIDATION_FAILED"
        )


@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE"])
@pytest.mark.parametrize(
    "path", ["/api/v1/device-auth/pair", "/api/v1/device-auth/refresh"]
)
def test_csrf_exemption_requires_post_and_exact_device_auth_path(
    device_client: TestClient, method: str, path: str
) -> None:
    """A path-only exemption would silently exempt unsupported unsafe methods."""
    _assert_error(
        device_client.request(method, path, json={}),
        403,
        "CSRF_VALIDATION_FAILED",
    )


@pytest.mark.asyncio
async def test_authenticated_owner_denials_are_safely_audited_but_anonymous_is_not(
    device_client: TestClient, db_session: AsyncSession, active_owner: User
) -> None:
    """Authenticated OWNER probes need evidence without persisting headers or credentials."""
    project = Project(name="Device denial audit")
    staff = local_user("staff-1", display_name="Staff")
    db_session.add_all([project, staff])
    await db_session.commit()
    pair = await _pair_device(device_client, db_session, active_owner.id, project.id)
    request_ids = {
        "device.pairing_code.create": uuid4(),
        "device.list": uuid4(),
        "device.revoke": uuid4(),
    }

    _login(device_client, "staff-code")
    _assert_error(
        device_client.post(
            "/api/v1/owner/devices/pairing-codes",
            json={"project_ids": [str(project.id)]},
            headers={
                **_csrf(device_client),
                "X-Request-ID": str(request_ids["device.pairing_code.create"]),
            },
        ),
        403,
        "OWNER_REQUIRED",
    )
    device_client.cookies.clear()
    device_headers = {"Authorization": f"Bearer {pair.access_token}"}
    _assert_error(
        device_client.get(
            "/api/v1/owner/devices",
            headers={
                **device_headers,
                "X-Request-ID": str(request_ids["device.list"]),
            },
        ),
        403,
        "OWNER_REQUIRED",
    )
    _assert_error(
        device_client.delete(
            f"/api/v1/owner/devices/{pair.device_id}",
            headers={
                **device_headers,
                "X-Request-ID": str(request_ids["device.revoke"]),
            },
        ),
        403,
        "OWNER_REQUIRED",
    )
    anonymous_request_id = uuid4()
    _assert_error(
        device_client.get(
            "/api/v1/owner/devices",
            headers={"X-Request-ID": str(anonymous_request_id)},
        ),
        401,
        "AUTHENTICATION_REQUIRED",
    )

    denied = list(
        await db_session.scalars(
            select(AuditLog)
            .where(
                AuditLog.request_id.in_(tuple(request_ids.values())),
                AuditLog.outcome == "DENIED",
            )
            .order_by(AuditLog.action)
        )
    )
    assert {(event.action, event.actor_kind, event.actor_id) for event in denied} == {
        ("device.pairing_code.create", "user", staff.id),
        ("device.list", "device", pair.device_id),
        ("device.revoke", "device", pair.device_id),
    }
    assert all(event.metadata_json.get("reason") == "OWNER_REQUIRED" for event in denied)
    serialized = str([event.metadata_json for event in denied]).lower()
    assert pair.access_token not in serialized
    assert all(secret not in serialized for secret in ("authorization", "token", "hash"))
    assert not await db_session.scalar(
        select(AuditLog.id).where(AuditLog.request_id == anonymous_request_id)
    )
    db_session.expire_all()
    device = await db_session.get(DeviceConnection, pair.device_id)
    assert device is not None and device.revoked_at is None


@pytest.mark.asyncio
async def test_me_uses_live_active_project_names_and_updates_last_used(
    device_client: TestClient, db_session: AsyncSession, active_owner: User
) -> None:
    """Token snapshots must not expose archived or stale project names and activity."""
    active = Project(name="Live me")
    archived = Project(name="Archived me")
    db_session.add_all([active, archived])
    await db_session.commit()
    pair = await _pair_device(device_client, db_session, active_owner.id, active.id)
    service = await _device_service(device_client, db_session)
    second_code = await service.create_pairing_code(
        active_owner.id, [archived.id], request_id=uuid4()
    )
    second_pair = await service.pair(second_code.raw_code, "Archive-PC", request_id=uuid4())
    archived.status = ProjectStatus.ARCHIVED
    active.name = "Live me renamed"
    await db_session.commit()

    before = await db_session.get(DeviceConnection, pair.device_id)
    assert before is not None and before.last_used_at is None
    response = device_client.get(
        "/api/v1/device-auth/me",
        headers={"Authorization": f"Bearer {pair.access_token}"},
    )
    archived_response = device_client.get(
        "/api/v1/device-auth/me",
        headers={"Authorization": f"Bearer {second_pair.access_token}"},
    )
    assert response.status_code == archived_response.status_code == 200
    assert response.json()["projects"] == [
        {"id": str(active.id), "name": "Live me renamed"}
    ]
    assert archived_response.json()["projects"] == []
    db_session.expire_all()
    after = await db_session.get(DeviceConnection, pair.device_id)
    assert after is not None and after.last_used_at is not None
    use_event = await db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "device.use", AuditLog.actor_id == pair.device_id
        )
    )
    assert use_event is not None


@pytest.mark.asyncio
async def test_browser_actor_denied_from_device_me_is_safely_audited(
    device_client: TestClient, db_session: AsyncSession, active_owner: User
) -> None:
    """An authenticated browser probe must not disappear from the denial trail."""
    request_id = uuid4()
    await db_session.commit()
    _login(device_client)

    response = device_client.get(
        "/api/v1/device-auth/me", headers={"X-Request-ID": str(request_id)}
    )

    _assert_error(response, 401, "AUTHENTICATION_REQUIRED")
    denied = await db_session.scalar(
        select(AuditLog).where(
            AuditLog.request_id == request_id,
            AuditLog.action == "device.me",
            AuditLog.outcome == "DENIED",
        )
    )
    assert denied is not None
    assert denied.actor_kind == "user" and denied.actor_id == active_owner.id
    assert denied.metadata_json == {
        "actor_role": "OWNER",
        "reason": "DEVICE_CREDENTIAL_REQUIRED",
    }
    serialized = str(denied.metadata_json).lower()
    assert all(secret not in serialized for secret in ("authorization", "token", "hash"))


def test_browser_device_me_denial_does_not_succeed_when_audit_fails(
    device_client: TestClient,
) -> None:
    """Returning 401 without mandatory evidence would make authenticated probes invisible."""
    _login(device_client)

    def fail_denial(_mapper: object, _connection: object, target: AuditLog) -> None:
        if target.action == "device.me" and target.outcome == "DENIED":
            raise RuntimeError("audit unavailable")

    event.listen(AuditLog, "before_insert", fail_denial)
    try:
        response = device_client.get("/api/v1/device-auth/me")
    finally:
        event.remove(AuditLog, "before_insert", fail_denial)

    _assert_error(response, 500, "REQUEST_FAILED")


@pytest.mark.asyncio
async def test_delete_is_csrf_protected_idempotent_and_revokes_all_credentials(
    device_client: TestClient, db_session: AsyncSession, active_owner: User
) -> None:
    """A partial or non-idempotent revoke could leave a rotated credential active."""
    project = Project(name="HTTP revoke")
    db_session.add(project)
    await db_session.commit()
    pair = await _pair_device(device_client, db_session, active_owner.id, project.id)
    service = await _device_service(device_client, db_session)
    rotated = await service.refresh(pair.refresh_token, request_id=uuid4())
    _login(device_client)

    _assert_error(
        device_client.delete(f"/api/v1/owner/devices/{pair.device_id}"),
        403,
        "CSRF_VALIDATION_FAILED",
    )
    first = device_client.delete(
        f"/api/v1/owner/devices/{pair.device_id}", headers=_csrf(device_client)
    )
    second = device_client.delete(
        f"/api/v1/owner/devices/{pair.device_id}", headers=_csrf(device_client)
    )
    assert first.status_code == second.status_code == 204
    assert first.content == second.content == b""
    device_client.cookies.clear()
    for token in (pair.access_token, rotated.access_token):
        _assert_error(
            device_client.get(
                "/api/v1/device-auth/me",
                headers={"Authorization": f"Bearer {token}"},
            ),
            401,
            "AUTHENTICATION_REQUIRED",
        )
    for token in (pair.refresh_token, rotated.refresh_token):
        _assert_error(
            device_client.post(
                "/api/v1/device-auth/refresh", json={"refresh_token": token}
            ),
            401,
            "AUTHENTICATION_REQUIRED",
        )


@pytest.mark.asyncio
async def test_invalid_reused_and_expired_pair_credentials_share_one_safe_401(
    device_client: TestClient, db_session: AsyncSession, active_owner: User
) -> None:
    """Pair responses must not disclose existence, expiry, or prior consumption."""
    project = Project(name="Pair safe failures")
    db_session.add(project)
    await db_session.commit()
    service = await _device_service(device_client, db_session)
    replay = await service.create_pairing_code(active_owner.id, [project.id], request_id=uuid4())
    await service.pair(replay.raw_code, "First-PC", request_id=uuid4())
    expired = await service.create_pairing_code(active_owner.id, [project.id], request_id=uuid4())
    async with _test_factory(db_session)() as session, session.begin():
        row = await session.scalar(
            select(DevicePairingCode).where(
                DevicePairingCode.code_hash == hash_token(expired.raw_code)
            )
        )
        assert row is not None
        row.created_at = datetime.now(UTC) - timedelta(minutes=20)
        row.expires_at = datetime.now(UTC) - timedelta(minutes=10)

    responses = [
        device_client.post(
            "/api/v1/device-auth/pair",
            json={"pairing_code": raw, "device_name": "Denied-PC"},
        )
        for raw in ("invalid", replay.raw_code, expired.raw_code)
    ]
    for response in responses:
        _assert_error(response, 401, "AUTHENTICATION_REQUIRED")
    assert {(item.json()["error"]["code"], item.json()["error"]["message"]) for item in responses} == {
        ("AUTHENTICATION_REQUIRED", "Authentication required")
    }
    assert all(raw not in str(response.json()) for raw, response in zip(
        ("invalid", replay.raw_code, expired.raw_code), responses, strict=True
    ))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "body", "action"),
    [
        (
            "/api/v1/device-auth/pair",
            {"pairing_code": "\ud800" * 32, "device_name": "Unicode-PC"},
            "device.pair",
        ),
        (
            "/api/v1/device-auth/refresh",
            {"refresh_token": "\ud800" * 32},
            "device.refresh",
        ),
    ],
)
async def test_unpaired_unicode_surrogate_credentials_share_safe_401_and_denied_audit(
    device_client: TestClient,
    db_session: AsyncSession,
    path: str,
    body: dict[str, str],
    action: str,
) -> None:
    """Every JSON string must stay inside the uniform invalid-credential boundary."""
    request_id = uuid4()
    response = device_client.post(
        path,
        content=json.dumps(body, ensure_ascii=True).encode("ascii"),
        headers={
            "Content-Type": "application/json",
            "X-Request-ID": str(request_id),
        },
    )

    _assert_error(response, 401, "AUTHENTICATION_REQUIRED")
    denied = await db_session.scalar(
        select(AuditLog).where(
            AuditLog.request_id == request_id,
            AuditLog.action == action,
            AuditLog.outcome == "DENIED",
        )
    )
    assert denied is not None
    assert denied.actor_kind == "system" and denied.actor_id is None
    assert denied.metadata_json == {
        "actor_role": None,
        "reason": "INVALID_CREDENTIAL",
    }
    serialized = str(denied.metadata_json).lower()
    assert all(secret not in serialized for secret in ("authorization", "token", "hash"))


@pytest.mark.asyncio
async def test_invalid_reused_and_expired_refresh_credentials_share_one_safe_401(
    device_client: TestClient, db_session: AsyncSession, active_owner: User
) -> None:
    """Refresh responses must not reveal token state or database matching details."""
    project = Project(name="Refresh safe failures")
    db_session.add(project)
    await db_session.commit()
    reused_pair = await _pair_device(
        device_client, db_session, active_owner.id, project.id, name="Reuse-PC"
    )
    expired_pair = await _pair_device(
        device_client, db_session, active_owner.id, project.id, name="Expire-PC"
    )
    service = await _device_service(device_client, db_session)
    await service.refresh(reused_pair.refresh_token, request_id=uuid4())
    now = datetime.now(UTC)
    async with _test_factory(db_session)() as session, session.begin():
        expired_session = await session.scalar(
            select(DeviceSession).where(
                DeviceSession.refresh_token_hash == hash_token(expired_pair.refresh_token)
            )
        )
        assert expired_session is not None
        expired_session.created_at = now - timedelta(days=15)
        expired_session.access_expires_at = now - timedelta(days=14, hours=22)
        expired_session.refresh_expires_at = now - timedelta(days=1)

    raw_tokens = ("invalid", reused_pair.refresh_token, expired_pair.refresh_token)
    responses = [
        device_client.post(
            "/api/v1/device-auth/refresh", json={"refresh_token": raw}
        )
        for raw in raw_tokens
    ]
    for response in responses:
        _assert_error(response, 401, "AUTHENTICATION_REQUIRED")
    assert {(item.json()["error"]["code"], item.json()["error"]["message"]) for item in responses} == {
        ("AUTHENTICATION_REQUIRED", "Authentication required")
    }
    assert all(raw not in str(response.json()) for raw, response in zip(raw_tokens, responses, strict=True))


@pytest.mark.asyncio
async def test_request_validation_and_device_name_normalization(
    device_client: TestClient, db_session: AsyncSession, active_owner: User
) -> None:
    """Malformed grants and unsafe names must fail before creating credential state."""
    project = Project(name="Device validation")
    db_session.add(project)
    await db_session.commit()
    _login(device_client)
    for project_ids in ([], [str(project.id), str(project.id)], ["not-a-uuid"]):
        _assert_error(
            device_client.post(
                "/api/v1/owner/devices/pairing-codes",
                json={"project_ids": project_ids},
                headers=_csrf(device_client),
            ),
            422,
            "VALIDATION_ERROR",
        )
    device_client.cookies.clear()
    service = await _device_service(device_client, db_session)
    code = await service.create_pairing_code(active_owner.id, [project.id], request_id=uuid4())
    for name in ("   ", "x" * 129, "PC\x00x", "PC\nx"):
        _assert_error(
            device_client.post(
                "/api/v1/device-auth/pair",
                json={"pairing_code": code.raw_code, "device_name": name},
            ),
            422,
            "VALIDATION_ERROR",
        )
    normalized_code = await service.create_pairing_code(
        active_owner.id, [project.id], request_id=uuid4()
    )
    paired = device_client.post(
        "/api/v1/device-auth/pair",
        json={"pairing_code": normalized_code.raw_code, "device_name": "  Ｏｗｎｅｒ－ＰＣ  "},
    )
    assert paired.status_code == 200
    me = device_client.get(
        "/api/v1/device-auth/me",
        headers={"Authorization": f"Bearer {paired.json()['access_token']}"},
    )
    assert me.status_code == 200 and me.json()["name"] == "Owner-PC"


@pytest.mark.asyncio
async def test_invalid_device_name_is_audited_without_consuming_pairing_code(
    device_client: TestClient, db_session: AsyncSession, active_owner: User
) -> None:
    """Schema-first rejection would omit failed-pair evidence for a real unused code."""
    project = Project(name="Invalid device name audit")
    db_session.add(project)
    await db_session.commit()
    service = await _device_service(device_client, db_session)
    code = await service.create_pairing_code(
        active_owner.id, [project.id], request_id=uuid4()
    )
    request_id = uuid4()

    response = device_client.post(
        "/api/v1/device-auth/pair",
        json={"pairing_code": code.raw_code, "device_name": "   "},
        headers={"X-Request-ID": str(request_id)},
    )

    _assert_error(response, 422, "VALIDATION_ERROR")
    pairing = await db_session.scalar(
        select(DevicePairingCode).where(
            DevicePairingCode.code_hash == hash_token(code.raw_code)
        )
    )
    denied = await db_session.scalar(
        select(AuditLog).where(
            AuditLog.request_id == request_id,
            AuditLog.action == "device.pair",
            AuditLog.outcome == "DENIED",
        )
    )
    assert pairing is not None and pairing.used_at is None
    assert denied is not None
    assert denied.actor_kind == "system" and denied.actor_id is None
    assert denied.metadata_json == {"actor_role": None, "reason": "INVALID_REQUEST"}
    serialized = str(denied.metadata_json).lower()
    assert code.raw_code not in serialized
    assert all(secret not in serialized for secret in ("authorization", "token", "hash"))


@pytest.mark.asyncio
async def test_invalid_device_name_does_not_return_422_when_denial_audit_fails(
    device_client: TestClient, db_session: AsyncSession, active_owner: User
) -> None:
    """A failed mandatory denial write must propagate while leaving the code unused."""
    project = Project(name="Invalid name audit fault")
    db_session.add(project)
    await db_session.commit()
    service = await _device_service(device_client, db_session)
    code = await service.create_pairing_code(
        active_owner.id, [project.id], request_id=uuid4()
    )

    def fail_denial(_mapper: object, _connection: object, target: AuditLog) -> None:
        if target.action == "device.pair" and target.outcome == "DENIED":
            raise RuntimeError("audit unavailable")

    event.listen(AuditLog, "before_insert", fail_denial)
    try:
        response = device_client.post(
            "/api/v1/device-auth/pair",
            json={"pairing_code": code.raw_code, "device_name": "PC\x00x"},
        )
    finally:
        event.remove(AuditLog, "before_insert", fail_denial)

    _assert_error(response, 500, "REQUEST_FAILED")
    pairing = await db_session.scalar(
        select(DevicePairingCode).where(
            DevicePairingCode.code_hash == hash_token(code.raw_code)
        )
    )
    assert pairing is not None and pairing.used_at is None


@pytest.mark.asyncio
async def test_reused_request_id_cannot_suppress_distinct_device_use_events(
    device_client: TestClient, db_session: AsyncSession, active_owner: User
) -> None:
    """A client-controlled correlation ID must not deduplicate separate successful uses."""
    project = Project(name="Repeated request ID use audit")
    db_session.add(project)
    await db_session.commit()
    pair = await _pair_device(device_client, db_session, active_owner.id, project.id)
    request_id = uuid4()
    headers = {
        "Authorization": f"Bearer {pair.access_token}",
        "X-Request-ID": str(request_id),
    }

    first = device_client.get("/api/v1/device-auth/me", headers=headers)
    second = device_client.get("/api/v1/device-auth/me", headers=headers)

    assert first.status_code == second.status_code == 200
    events = list(
        await db_session.scalars(
            select(AuditLog).where(
                AuditLog.request_id == request_id,
                AuditLog.action == "device.use",
                AuditLog.outcome == "SUCCESS",
            )
        )
    )
    assert len(events) == 2
    assert all(event.actor_id == pair.device_id for event in events)
