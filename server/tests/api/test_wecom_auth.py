"""End-to-end browser authentication security tests."""

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from superboss.core.config import Settings
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

    assert _callback(api_client, "owner-code", None).status_code == 400
    assert (
        _callback(api_client, "owner-code", f"wrong-{started.json()['state']}").status_code == 400
    )


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
