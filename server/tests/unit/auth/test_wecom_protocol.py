"""Externally observable WeCom HTTP protocol validation."""

import httpx
import pytest

from superboss.core.config import Settings
from superboss.infrastructure.wecom import WeComError, WeComIdentityProvider


def _settings() -> Settings:
    return Settings(wecom_corp_id="corp", wecom_corp_secret="secret")


@pytest.mark.asyncio
async def test_exchange_code_sends_exact_official_requests() -> None:
    """Changing either endpoint, method, or required query parameter breaks login."""
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        if request.url.path == "/cgi-bin/gettoken":
            assert dict(request.url.params) == {"corpid": "corp", "corpsecret": "secret"}
            return httpx.Response(200, json={"errcode": 0, "access_token": "server-token"})
        assert request.url.path == "/cgi-bin/auth/getuserinfo"
        assert dict(request.url.params) == {"access_token": "server-token", "code": "oauth-code"}
        return httpx.Response(200, json={"errcode": 0, "userid": "owner-1"})

    provider = WeComIdentityProvider(_settings(), transport=httpx.MockTransport(handler))
    assert (await provider.exchange_code("oauth-code")).userid == "owner-1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload,status",
    [
        ({"errcode": 0, "access_token": "x"}, 500),
        (None, 200),
        ([], 200),
        ({"errcode": 1}, 200),
        ({"errcode": 0}, 200),
        ({"errcode": 0, "access_token": ""}, 200),
        ({"errcode": 0, "access_token": 1}, 200),
    ],
)
async def test_token_stage_rejects_invalid_payloads(payload: object, status: int) -> None:
    """Relaxing token-stage response validation must fail before an identity exists."""
    def handler(_: httpx.Request) -> httpx.Response:
        if payload is None:
            return httpx.Response(status, content=b"not-json")
        return httpx.Response(status, json=payload)

    with pytest.raises(WeComError):
        await WeComIdentityProvider(_settings(), transport=httpx.MockTransport(handler)).exchange_code("code")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload,status",
    [
        ({"errcode": 0, "userid": "staff"}, 500),
        (None, 200),
        ([], 200),
        ({"errcode": 1}, 200),
        ({"errcode": 0}, 200),
        ({"errcode": 0, "userid": ""}, 200),
        ({"errcode": 0, "userid": 1}, 200),
        ({"errcode": 0, "UserId": "legacy"}, 200),
    ],
)
async def test_identity_stage_rejects_invalid_payloads(payload: object, status: int) -> None:
    """Identity payload defects must not be mistaken for an authenticated userid."""
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(200, json={"errcode": 0, "access_token": "server-token"})
        if payload is None:
            return httpx.Response(status, content=b"not-json")
        return httpx.Response(status, json=payload)

    with pytest.raises(WeComError):
        await WeComIdentityProvider(_settings(), transport=httpx.MockTransport(handler)).exchange_code("code")
