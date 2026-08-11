"""End-to-end local browser authentication contracts."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import jwt
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from superboss.core.config import Settings
from superboss.main import create_app
from superboss.modules.audit.models import AuditLog
from superboss.modules.auth.models import AuthSession
from superboss.modules.auth.passwords import hash_password
from superboss.modules.users.models import Role, User, UserStatus

VALID_PASSWORD = "correct horse battery staple"
NEW_PASSWORD = "moonlight forest replacement phrase"


@pytest_asyncio.fixture
async def local_auth_client(
    db_session: AsyncSession, test_settings: Settings
) -> AsyncIterator[TestClient]:
    del db_session
    app = create_app(test_settings)
    with TestClient(app, base_url="https://testserver") as client:
        yield client


async def _user(
    session: AsyncSession,
    *,
    username: str = "owner",
    password: str = VALID_PASSWORD,
    role: Role = Role.OWNER,
    status: UserStatus = UserStatus.ACTIVE,
    must_change_password: bool = False,
) -> User:
    user = User(
        username=username,
        display_name="Owner" if role == Role.OWNER else "Staff",
        password_hash=hash_password(password),
        password_changed_at=datetime.now(UTC),
        must_change_password=must_change_password,
        role=role,
        status=status,
    )
    session.add(user)
    await session.commit()
    return user


def _csrf(client: TestClient) -> str:
    response = client.get("/api/v1/auth/csrf")
    assert response.status_code == 204
    assert response.content == b""
    value = client.cookies.get("XSRF-TOKEN")
    assert value is not None and len(value) >= 32
    return value


def _login(client: TestClient, username: str = "owner", password: str = VALID_PASSWORD):
    csrf = _csrf(client)
    return client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
        headers={"X-CSRF-Token": csrf},
    )


def _safe_error(response: httpx.Response, status: int, code: str) -> None:
    assert response.status_code == status
    body = response.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "request_id"}
    assert body["error"]["code"] == code


def test_openapi_exposes_only_local_browser_auth_routes(local_auth_client: TestClient) -> None:
    paths = local_auth_client.get("/openapi.json").json()["paths"]

    assert {
        "/api/v1/auth/csrf",
        "/api/v1/auth/login",
        "/api/v1/auth/logout",
        "/api/v1/auth/me",
        "/api/v1/auth/password/change",
        "/api/v1/auth/refresh",
    } <= set(paths)
    assert not any("wecom" in path or "callback" in path for path in paths)


def test_login_requires_exact_csrf_and_strict_body(local_auth_client: TestClient) -> None:
    missing = local_auth_client.post(
        "/api/v1/auth/login", json={"username": "owner", "password": VALID_PASSWORD}
    )
    _safe_error(missing, 403, "CSRF_VALIDATION_FAILED")

    csrf = _csrf(local_auth_client)
    extra = local_auth_client.post(
        "/api/v1/auth/login",
        json={"username": "owner", "password": VALID_PASSWORD, "role": "OWNER"},
        headers={"X-CSRF-Token": csrf},
    )
    _safe_error(extra, 422, "VALIDATION_ERROR")


@pytest.mark.asyncio
async def test_successful_login_sets_exact_session_and_records_audit(
    local_auth_client: TestClient,
    db_session: AsyncSession,
    test_settings: Settings,
) -> None:
    user = await _user(db_session)
    request_id = uuid4()
    csrf = _csrf(local_auth_client)

    response = local_auth_client.post(
        "/api/v1/auth/login",
        json={"username": "owner", "password": VALID_PASSWORD},
        headers={"X-CSRF-Token": csrf, "X-Request-ID": str(request_id)},
    )

    assert response.status_code == 204 and response.content == b""
    access = local_auth_client.cookies.get("access_token")
    refresh = local_auth_client.cookies.get("refresh_token")
    assert access is not None and refresh is not None
    claims = jwt.decode(
        access,
        test_settings.jwt_secret,
        algorithms=["HS256"],
        options={"verify_exp": False, "verify_iat": False},
    )
    assert claims["exp"] - claims["iat"] == 7200
    session = await db_session.scalar(select(AuthSession).where(AuthSession.user_id == user.id))
    assert session is not None
    assert session.access_expires_at == datetime.fromtimestamp(claims["exp"], UTC)
    assert session.refresh_expires_at - session.created_at <= timedelta(days=14, seconds=1)
    event = await db_session.scalar(select(AuditLog).where(AuditLog.request_id == request_id))
    assert event is not None
    assert (event.action, event.outcome, event.actor_id, event.object_id) == (
        "auth.login",
        "SUCCESS",
        user.id,
        user.id,
    )
    assert event.metadata_json == {"actor_role": "OWNER"}

    me = local_auth_client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json() == {
        "display_name": "Owner",
        "must_change_password": False,
        "role": "OWNER",
        "username": "owner",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("variant", ["unknown", "wrong", "disabled"])
async def test_login_failures_are_uniform_and_set_no_credentials(
    local_auth_client: TestClient,
    db_session: AsyncSession,
    variant: str,
) -> None:
    if variant != "unknown":
        await _user(
            db_session,
            status=UserStatus.DISABLED if variant == "disabled" else UserStatus.ACTIVE,
        )
    response = _login(
        local_auth_client,
        username="missing" if variant == "unknown" else "owner",
        password="wrong password phrase" if variant == "wrong" else VALID_PASSWORD,
    )

    _safe_error(response, 401, "AUTHENTICATION_FAILED")
    assert local_auth_client.cookies.get("access_token") is None
    assert local_auth_client.cookies.get("refresh_token") is None
    assert VALID_PASSWORD not in response.text and "missing" not in response.text


@pytest.mark.asyncio
async def test_five_failures_temporarily_lock_the_user_with_same_public_error(
    local_auth_client: TestClient, db_session: AsyncSession
) -> None:
    user = await _user(db_session)

    for _ in range(5):
        _safe_error(
            _login(local_auth_client, password="wrong password phrase"),
            401,
            "AUTHENTICATION_FAILED",
        )
    _safe_error(_login(local_auth_client), 401, "AUTHENTICATION_FAILED")

    await db_session.refresh(user)
    assert user.failed_login_count == 5
    assert user.locked_until is not None and user.locked_until > datetime.now(UTC)


def test_me_refresh_hint_does_not_infer_session_from_csrf(local_auth_client: TestClient) -> None:
    csrf = _csrf(local_auth_client)
    anonymous = local_auth_client.get("/api/v1/auth/me")
    assert anonymous.status_code == 401
    assert anonymous.headers.get("X-SuperBoss-Refreshable") is None

    local_auth_client.cookies.set(
        "refresh_token", "opaque-invalid", domain="testserver.local", path="/api/v1/auth"
    )
    refreshable = local_auth_client.get("/api/v1/auth/me")
    assert refreshable.status_code == 401
    assert refreshable.headers.get("X-SuperBoss-Refreshable") == "1"
    assert csrf == local_auth_client.cookies.get("XSRF-TOKEN")


@pytest.mark.asyncio
async def test_required_password_change_denies_business_then_replaces_all_sessions(
    local_auth_client: TestClient, db_session: AsyncSession
) -> None:
    user = await _user(db_session, must_change_password=True)
    assert _login(local_auth_client).status_code == 204
    old_session = await db_session.scalar(
        select(AuthSession).where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
    )
    assert old_session is not None
    csrf = local_auth_client.cookies.get("XSRF-TOKEN")

    denied = local_auth_client.post(
        "/api/v1/projects",
        json={"name": "Blocked before password change", "is_test": True},
        headers={"X-CSRF-Token": csrf},
    )
    _safe_error(denied, 403, "PASSWORD_CHANGE_REQUIRED")

    changed = local_auth_client.post(
        "/api/v1/auth/password/change",
        json={"current_password": VALID_PASSWORD, "new_password": NEW_PASSWORD},
        headers={"X-CSRF-Token": csrf},
    )
    assert changed.status_code == 204 and changed.content == b""
    await db_session.refresh(user)
    await db_session.refresh(old_session)
    assert user.must_change_password is False
    assert old_session.revoked_at is not None
    live = (
        await db_session.scalars(
            select(AuthSession).where(
                AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None)
            )
        )
    ).all()
    assert len(live) == 1 and live[0].id != old_session.id
    assert local_auth_client.get("/api/v1/auth/me").json()["must_change_password"] is False


@pytest.mark.asyncio
async def test_password_change_rejects_wrong_current_and_reuse_without_mutation(
    local_auth_client: TestClient, db_session: AsyncSession
) -> None:
    user = await _user(db_session, must_change_password=True)
    original_hash = user.password_hash
    assert _login(local_auth_client).status_code == 204
    csrf = local_auth_client.cookies.get("XSRF-TOKEN")

    wrong = local_auth_client.post(
        "/api/v1/auth/password/change",
        json={"current_password": "wrong password phrase", "new_password": NEW_PASSWORD},
        headers={"X-CSRF-Token": csrf},
    )
    _safe_error(wrong, 401, "AUTHENTICATION_FAILED")
    reused = local_auth_client.post(
        "/api/v1/auth/password/change",
        json={"current_password": VALID_PASSWORD, "new_password": VALID_PASSWORD},
        headers={"X-CSRF-Token": csrf},
    )
    _safe_error(reused, 422, "PASSWORD_REUSE_FORBIDDEN")
    await db_session.refresh(user)
    assert user.password_hash == original_hash and user.must_change_password is True


@pytest.mark.asyncio
async def test_login_audit_failure_sets_no_browser_credentials(
    monkeypatch: pytest.MonkeyPatch,
    db_session: AsyncSession,
    test_settings: Settings,
) -> None:
    import superboss.modules.auth.router as auth_router

    await _user(db_session)

    async def fail_audit(*_arguments: object, **_keywords: object) -> None:
        raise RuntimeError("AUDIT-SYNTHETIC-PRIVATE-DETAIL")

    monkeypatch.setattr(auth_router, "_stage_auth_audit", fail_audit, raising=False)
    app = create_app(test_settings)
    with TestClient(
        app, base_url="https://testserver", raise_server_exceptions=False
    ) as client:
        response = _login(client)

    assert response.status_code == 500
    assert "access_token=" not in response.headers.get("set-cookie", "")
    assert "refresh_token=" not in response.headers.get("set-cookie", "")
    assert "AUDIT-SYNTHETIC-PRIVATE-DETAIL" not in response.text
    assert await db_session.scalar(select(AuthSession)) is None


@pytest.mark.asyncio
async def test_password_change_audit_failure_rolls_back_hash_and_sessions(
    monkeypatch: pytest.MonkeyPatch,
    db_session: AsyncSession,
    test_settings: Settings,
) -> None:
    import superboss.modules.auth.router as auth_router

    user = await _user(db_session, must_change_password=True)
    user_id = user.id
    app = create_app(test_settings)
    with TestClient(
        app, base_url="https://testserver", raise_server_exceptions=False
    ) as client:
        assert _login(client).status_code == 204
        original_hash = user.password_hash
        original_session = await db_session.scalar(
            select(AuthSession).where(
                AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None)
            )
        )
        assert original_session is not None

        async def fail_audit(*_arguments: object, **_keywords: object) -> None:
            raise RuntimeError("PASSWORD-AUDIT-SYNTHETIC-PRIVATE-DETAIL")

        monkeypatch.setattr(auth_router, "_stage_auth_audit", fail_audit, raising=False)
        response = client.post(
            "/api/v1/auth/password/change",
            json={"current_password": VALID_PASSWORD, "new_password": NEW_PASSWORD},
            headers={"X-CSRF-Token": str(client.cookies.get("XSRF-TOKEN"))},
        )

    assert response.status_code == 500
    assert "access_token=" not in response.headers.get("set-cookie", "")
    assert "refresh_token=" not in response.headers.get("set-cookie", "")
    assert "PASSWORD-AUDIT-SYNTHETIC-PRIVATE-DETAIL" not in response.text
    db_session.expire_all()
    persisted = await db_session.get(User, user_id)
    assert persisted is not None
    assert persisted.password_hash == original_hash
    assert persisted.must_change_password is True
    await db_session.refresh(original_session)
    assert original_session.revoked_at is None
