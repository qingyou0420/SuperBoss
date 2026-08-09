"""End-to-end browser authentication security tests."""

import asyncio
from collections.abc import AsyncIterator

import httpx
import jwt
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from superboss.core.config import Settings
from superboss.core.security import hash_token, utcnow
from superboss.main import create_app
from superboss.modules.auth.models import AuthSession, OAuthState
from superboss.modules.users.models import Role, User, UserStatus


@pytest_asyncio.fixture
async def api_client(
    db_session: AsyncSession, test_settings: Settings
) -> AsyncIterator[TestClient]:
    app = create_app(test_settings)
    with TestClient(app, base_url="https://testserver") as client:
        yield client
    await db_session.rollback()
    await db_session.execute(delete(OAuthState))
    await db_session.execute(delete(AuthSession))
    await db_session.execute(delete(User))
    await db_session.commit()


def _callback(client: TestClient, code: str, state: str | None) -> object:
    params = {"code": code}
    if state is not None:
        params["state"] = state
    return client.get("/api/v1/auth/wecom/callback", params=params, follow_redirects=False)


@pytest.mark.asyncio
async def test_configured_owner_bootstraps_the_only_owner(
    api_client: TestClient, db_session: AsyncSession
) -> None:
    """Removing bootstrap or the OWNER role assignment fails this first-login flow."""
    started = api_client.get("/api/v1/auth/wecom/start")
    response = _callback(api_client, "owner-code", started.json()["state"])

    assert response.status_code == 204
    assert "wecom_oauth_state=\"\"" in response.headers["set-cookie"]
    assert api_client.cookies.get("wecom_oauth_state") is None
    users = (await db_session.scalars(select(User))).all()
    assert [(user.wecom_userid, user.role, user.status) for user in users] == [
        ("owner-1", Role.OWNER, UserStatus.ACTIVE)
    ]


@pytest.mark.asyncio
async def test_unknown_identity_is_forbidden_without_inserting_a_user(
    api_client: TestClient, db_session: AsyncSession
) -> None:
    """Accidentally provisioning arbitrary OAuth identities must be rejected."""
    started = api_client.get("/api/v1/auth/wecom/start")
    response = _callback(api_client, "unknown-code", started.json()["state"])

    assert response.status_code == 403
    assert (await db_session.scalars(select(User))).all() == []


@pytest.mark.asyncio
async def test_disabled_user_is_forbidden(api_client: TestClient, db_session: AsyncSession) -> None:
    """Removing the status check would grant a disabled whitelist account a session."""
    db_session.add(
        User(
            wecom_userid="staff-1",
            role=Role.STAFF,
            status=UserStatus.DISABLED,
            display_name="Staff",
        )
    )
    await db_session.commit()
    started = api_client.get("/api/v1/auth/wecom/start")
    response = _callback(api_client, "staff-code", started.json()["state"])

    assert response.status_code == 403


def test_callback_rejects_missing_or_mismatched_state(api_client: TestClient) -> None:
    """Dropping state validation enables login CSRF and must fail this boundary test."""
    started = api_client.get("/api/v1/auth/wecom/start")

    missing = _callback(api_client, "owner-code", None)
    assert missing.status_code == 400 and "Max-Age=0" in missing.headers["set-cookie"]
    api_client.cookies.set("wecom_oauth_state", started.cookies.get("wecom_oauth_state"), domain="testserver.local", path="/api/v1/auth/wecom")
    mismatch = _callback(api_client, "owner-code", f"wrong-{started.json()['state']}")
    assert mismatch.status_code == 400 and "Max-Age=0" in mismatch.headers["set-cookie"]


def test_missing_state_cookie_still_returns_deletion_instruction(api_client: TestClient) -> None:
    """Missing cookies still need an explicit deletion response for stale browser paths."""
    response = _callback(api_client, "owner-code", "some-state")
    assert response.status_code == 400
    assert "Max-Age=0" in response.headers["set-cookie"]


def test_browser_mutations_require_matching_csrf_token(api_client: TestClient) -> None:
    """Removing CSRF middleware would permit cookie-authenticated mutations."""
    for method in ("post", "put", "patch", "delete"):
        response = getattr(api_client, method)("/api/v1/auth/logout")
        assert response.status_code == 403
    api_client.cookies.set("XSRF-TOKEN", "expected", domain="testserver.local", path="/")
    assert (
        api_client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": "wrong"}).status_code == 403
    )


def test_oauth_state_failure_is_committed_and_cookie_is_cleared(api_client: TestClient) -> None:
    """A failed identity callback must consume state and make a restored-cookie replay fail."""
    started = api_client.get("/api/v1/auth/wecom/start")
    state = started.json()["state"]
    state_cookie = api_client.cookies.get("wecom_oauth_state")
    failed = _callback(api_client, "unknown-code", state)
    assert failed.status_code == 403
    assert "wecom_oauth_state=\"\"" in failed.headers["set-cookie"]
    api_client.cookies.set(
        "wecom_oauth_state", state_cookie, domain="testserver.local", path="/api/v1/auth/wecom"
    )
    replay = _callback(api_client, "owner-code", state)
    assert replay.status_code == 400
    assert "wecom_oauth_state=\"\"" in replay.headers["set-cookie"]


@pytest.mark.parametrize("code", ["unknown-code", "not-a-provider-code"])
def test_all_identity_failures_consume_state_once(api_client: TestClient, code: str) -> None:
    """Provider and authorization failures must not make a one-time state reusable."""
    started = api_client.get("/api/v1/auth/wecom/start")
    state = started.json()["state"]
    signed = api_client.cookies.get("wecom_oauth_state")
    failed = _callback(api_client, code, state)
    assert failed.status_code == 403
    assert "Max-Age=0" in failed.headers["set-cookie"]
    api_client.cookies.set("wecom_oauth_state", signed, domain="testserver.local", path="/api/v1/auth/wecom")
    assert _callback(api_client, "owner-code", state).status_code == 400


def test_callback_missing_code_clears_state_cookie(api_client: TestClient) -> None:
    """Optional callback parameters keep malformed callbacks inside the cookie-clearing handler."""
    started = api_client.get("/api/v1/auth/wecom/start")
    response = api_client.get("/api/v1/auth/wecom/callback", params={"state": started.json()["state"]})
    assert response.status_code == 400
    assert "Max-Age=0" in response.headers["set-cookie"]


@pytest.mark.asyncio
async def test_disabled_identity_consumes_state_and_cannot_retry(
    api_client: TestClient, db_session: AsyncSession
) -> None:
    """A disabled whitelist identity must not reactivate or reuse its consumed OAuth state."""
    db_session.add(User(wecom_userid="staff-1", role=Role.STAFF, status=UserStatus.DISABLED, display_name=""))
    await db_session.commit()
    started = api_client.get("/api/v1/auth/wecom/start")
    state, signed = started.json()["state"], api_client.cookies.get("wecom_oauth_state")
    denied = _callback(api_client, "staff-code", state)
    assert denied.status_code == 403 and "Max-Age=0" in denied.headers["set-cookie"]
    api_client.cookies.set("wecom_oauth_state", signed, domain="testserver.local", path="/api/v1/auth/wecom")
    assert _callback(api_client, "owner-code", state).status_code == 400
    disabled = await db_session.scalar(select(User).where(User.wecom_userid == "staff-1"))
    assert disabled is not None and disabled.status == UserStatus.DISABLED
    assert (await db_session.scalars(select(AuthSession))).all() == []


@pytest.mark.asyncio
async def test_database_expired_state_is_rejected_and_cookie_cleared(
    api_client: TestClient, db_session: AsyncSession
) -> None:
    """Ignoring persisted expiry would let an otherwise valid signed state be replayed."""
    started = api_client.get("/api/v1/auth/wecom/start")
    state, signed = started.json()["state"], api_client.cookies.get("wecom_oauth_state")
    await db_session.execute(update(OAuthState).where(OAuthState.nonce_hash == hash_token(state)).values(expires_at=utcnow()))
    await db_session.commit()
    expired = _callback(api_client, "owner-code", state)
    assert expired.status_code == 400 and "Max-Age=0" in expired.headers["set-cookie"]
    api_client.cookies.set("wecom_oauth_state", signed, domain="testserver.local", path="/api/v1/auth/wecom")
    assert _callback(api_client, "owner-code", state).status_code == 400


def test_signed_expired_state_is_rejected_and_cookie_cleared(api_client: TestClient, test_settings: Settings) -> None:
    """JWT expiry is independently enforced even while its database nonce is still live."""
    started = api_client.get("/api/v1/auth/wecom/start")
    state = started.json()["state"]
    expired = jwt.encode({"state": state, "iat": 0, "exp": 1}, test_settings.jwt_secret, algorithm="HS256")
    api_client.cookies.set("wecom_oauth_state", expired, domain="testserver.local", path="/api/v1/auth/wecom")
    response = _callback(api_client, "owner-code", state)
    assert response.status_code == 400 and "Max-Age=0" in response.headers["set-cookie"]


@pytest.mark.asyncio
async def test_concurrent_callbacks_consume_one_state_once(
    db_session: AsyncSession, test_settings: Settings
) -> None:
    """Two isolated browser clients must not both consume one OAuth state."""
    app = create_app(test_settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as starter:
        started = await starter.get("/api/v1/auth/wecom/start")
        state = started.json()["state"]
        signed = starter.cookies.get("wecom_oauth_state")
    barrier = asyncio.Barrier(2)

    async def callback() -> int:
        async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as client:
            client.cookies.set("wecom_oauth_state", signed, domain="testserver.local", path="/api/v1/auth/wecom")
            await barrier.wait()
            response = await client.get("/api/v1/auth/wecom/callback", params={"code": "owner-code", "state": state})
            return response.status_code

    assert sorted(await asyncio.gather(callback(), callback())) == [204, 400]
    replay_client = httpx.AsyncClient(transport=transport, base_url="https://testserver")
    try:
        replay_client.cookies.set("wecom_oauth_state", signed, domain="testserver.local", path="/api/v1/auth/wecom")
        assert (await replay_client.get("/api/v1/auth/wecom/callback", params={"code": "owner-code", "state": state})).status_code == 400
    finally:
        await replay_client.aclose()
    record = await db_session.scalar(select(OAuthState).where(OAuthState.nonce_hash == hash_token(state)))
    assert record is not None and record.consumed_at is not None


def test_logout_revokes_browser_access_and_refresh_tokens(api_client: TestClient) -> None:
    """Removing either server-side logout revocation leaves a cookie token usable."""
    started = api_client.get("/api/v1/auth/wecom/start")
    assert _callback(api_client, "owner-code", started.json()["state"]).status_code == 204
    refresh_token = api_client.cookies.get("refresh_token")
    access_token = api_client.cookies.get("access_token")
    csrf_token = api_client.cookies.get("XSRF-TOKEN")

    assert (
        api_client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf_token}).status_code
        == 204
    )
    api_client.cookies.set(
        "refresh_token", refresh_token, domain="testserver.local", path="/api/v1/auth"
    )
    api_client.cookies.set("access_token", access_token, domain="testserver.local", path="/")
    api_client.cookies.set("XSRF-TOKEN", csrf_token, domain="testserver.local", path="/")

    assert (
        api_client.post("/api/v1/auth/refresh", headers={"X-CSRF-Token": csrf_token}).status_code
        == 401
    )
    assert api_client.get("/api/v1/auth/me").status_code == 401


@pytest.mark.parametrize("authorization", [None, "junk", "Bearer nope", "Bearer invalid.token.value"])
def test_access_cookie_never_allows_bearer_to_bypass_csrf(
    api_client: TestClient, authorization: str | None
) -> None:
    """Changing auth-source precedence would let any header bypass browser CSRF."""
    started = api_client.get("/api/v1/auth/wecom/start")
    assert _callback(api_client, "owner-code", started.json()["state"]).status_code == 204
    headers = {} if authorization is None else {"Authorization": authorization}
    assert api_client.post("/api/v1/auth/logout", headers=headers).status_code == 403
    headers["X-CSRF-Token"] = api_client.cookies.get("XSRF-TOKEN")
    assert api_client.post("/api/v1/auth/logout", headers=headers).status_code == 204


@pytest.mark.parametrize("authorization", [None, "junk", "Bearer nope", "Bearer invalid.token.value"])
def test_refresh_cookie_never_allows_bearer_to_bypass_csrf(
    api_client: TestClient, authorization: str | None
) -> None:
    """Refresh-only browser sessions remain browser authentication for CSRF purposes."""
    started = api_client.get("/api/v1/auth/wecom/start")
    assert _callback(api_client, "owner-code", started.json()["state"]).status_code == 204
    api_client.cookies.delete("access_token", domain="testserver.local", path="/")
    headers = {} if authorization is None else {"Authorization": authorization}
    assert api_client.post("/api/v1/auth/refresh", headers=headers).status_code == 403
    headers["X-CSRF-Token"] = api_client.cookies.get("XSRF-TOKEN")
    assert api_client.post("/api/v1/auth/refresh", headers=headers).status_code == 204


def test_valid_bearer_cannot_bypass_csrf_when_both_browser_cookies_exist(api_client: TestClient) -> None:
    """A live device token must not change an already-cookie-authenticated request's source."""
    started = api_client.get("/api/v1/auth/wecom/start")
    assert _callback(api_client, "owner-code", started.json()["state"]).status_code == 204
    bearer = api_client.cookies.get("access_token")
    headers = {"Authorization": f"Bearer {bearer}"}
    assert api_client.post("/api/v1/auth/logout", headers=headers).status_code == 403
    headers["X-CSRF-Token"] = api_client.cookies.get("XSRF-TOKEN")
    assert api_client.post("/api/v1/auth/logout", headers=headers).status_code == 204


def _login_tokens(client: TestClient) -> tuple[str, str, str]:
    started = client.get("/api/v1/auth/wecom/start")
    assert _callback(client, "owner-code", started.json()["state"]).status_code == 204
    return (
        client.cookies.get("access_token"),
        client.cookies.get("refresh_token"),
        client.cookies.get("XSRF-TOKEN"),
    )


@pytest.mark.parametrize("cookie_shape", ["access", "refresh", "both"])
@pytest.mark.parametrize("auth_variant", ["absent", "arbitrary", "malformed", "wrong_signature", "live", "revoked"])
def test_every_browser_cookie_shape_requires_csrf_despite_authorization_headers(
    api_client: TestClient, cookie_shape: str, auth_variant: str
) -> None:
    """A future source-selection regression must not let any header bypass 18 cookie cases."""
    live_access, live_refresh, csrf = _login_tokens(api_client)
    assert api_client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 204
    revoked_access, _, _ = live_access, live_refresh, csrf
    live_access, live_refresh, _ = _login_tokens(api_client)
    api_client.cookies.clear()
    if cookie_shape in {"access", "both"}:
        api_client.cookies.set("access_token", live_access, domain="testserver.local", path="/")
    if cookie_shape in {"refresh", "both"}:
        api_client.cookies.set("refresh_token", live_refresh, domain="testserver.local", path="/api/v1/auth")
    headers = {
        "arbitrary": "junk",
        "malformed": "Bearer nope",
        "wrong_signature": f"Bearer {live_access}x",
        "live": f"Bearer {live_access}",
        "revoked": f"Bearer {revoked_access}",
    }
    request_headers = {} if auth_variant == "absent" else {"Authorization": headers[auth_variant]}
    assert api_client.post("/api/v1/auth/logout", headers=request_headers).status_code == 403


@pytest.mark.parametrize("auth_variant", ["absent", "arbitrary", "malformed", "wrong_signature", "live", "revoked"])
def test_header_only_bearer_matrix_uses_authoritative_server_state(
    api_client: TestClient, auth_variant: str
) -> None:
    """Only a live header-only session may bypass browser CSRF."""
    revoked_access, _, csrf = _login_tokens(api_client)
    assert api_client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 204
    live_access, _, _ = _login_tokens(api_client)
    api_client.cookies.clear()
    headers = {
        "arbitrary": "junk",
        "malformed": "Bearer nope",
        "wrong_signature": f"Bearer {live_access}x",
        "live": f"Bearer {live_access}",
        "revoked": f"Bearer {revoked_access}",
    }
    response = api_client.post(
        "/api/v1/auth/logout",
        headers={} if auth_variant == "absent" else {"Authorization": headers[auth_variant]},
    )
    assert response.status_code == (204 if auth_variant == "live" else (403 if auth_variant == "absent" else 401))
