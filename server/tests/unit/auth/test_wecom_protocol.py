"""Protocol-level WeCom adapter tests."""

import httpx
import pytest

from superboss.core.config import Settings
from superboss.infrastructure.wecom import WeComError, WeComIdentityProvider


@pytest.mark.asyncio
async def test_exchange_code_accepts_official_success_payload() -> None:
    """Using UserId or the obsolete endpoint would fail this official payload flow."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("gettoken"):
            return httpx.Response(200, json={"errcode": 0, "access_token": "server-token"})
        assert request.url.path.endswith("/auth/getuserinfo")
        return httpx.Response(200, json={"errcode": 0, "userid": "owner-1"})

    provider = WeComIdentityProvider(Settings(), transport=httpx.MockTransport(handler))

    assert (await provider.exchange_code("code")).userid == "owner-1"


@pytest.mark.asyncio
async def test_exchange_code_rejects_error_or_malformed_protocol_payloads() -> None:
    """Dropping HTTP/errcode/object checks would turn these provider failures into identities."""
    responses = [
        httpx.Response(200, json={"errcode": 40013}),
        httpx.Response(200, json=[]),
        httpx.Response(500, json={"errcode": 0, "access_token": "x"}),
    ]
    for response in responses:
        provider = WeComIdentityProvider(
            Settings(), transport=httpx.MockTransport(lambda _, response=response: response)
        )
        with pytest.raises(WeComError):
            await provider.exchange_code("code")
