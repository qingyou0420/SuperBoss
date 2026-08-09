"""Server-side WeCom OAuth identity providers."""

from dataclasses import dataclass
from typing import ClassVar
from urllib.parse import urlencode

import httpx

from superboss.core.config import Settings


class WeComError(Exception):
    """A safe, non-secret-bearing WeCom integration failure."""


@dataclass(frozen=True)
class WeComIdentity:
    userid: str


class WeComIdentityProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def authorization_url(self, state: str) -> str:
        query = urlencode(
            {
                "appid": self.settings.wecom_corp_id,
                "redirect_uri": self.settings.wecom_redirect_uri,
                "response_type": "code",
                "scope": "snsapi_base",
                "state": state,
                "agentid": self.settings.wecom_agent_id,
            }
        )
        return f"https://open.weixin.qq.com/connect/oauth2/authorize?{query}#wechat_redirect"

    async def exchange_code(self, code: str) -> WeComIdentity:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                token_response = await client.get(
                    "https://qyapi.weixin.qq.com/cgi-bin/gettoken",
                    params={
                        "corpid": self.settings.wecom_corp_id,
                        "corpsecret": self.settings.wecom_corp_secret,
                    },
                )
                token_data = token_response.json()
                access_token = token_data.get("access_token")
                if token_response.status_code != 200 or not isinstance(access_token, str):
                    raise WeComError("WeCom token exchange failed")
                user_response = await client.get(
                    "https://qyapi.weixin.qq.com/cgi-bin/user/getuserinfo",
                    params={"access_token": access_token, "code": code},
                )
                user_data = user_response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise WeComError("WeCom identity exchange failed") from error
        userid = user_data.get("UserId")
        if user_response.status_code != 200 or not isinstance(userid, str) or not userid:
            raise WeComError("WeCom identity exchange failed")
        return WeComIdentity(userid=userid)


class FakeWeComIdentityProvider:
    """Deterministic provider intentionally available only for test settings."""

    _codes: ClassVar[dict[str, str]] = {
        "owner-code": "owner-1",
        "staff-code": "staff-1",
        "unknown-code": "unknown-1",
    }

    def authorization_url(self, state: str) -> str:
        return f"https://fake.wecom.invalid/authorize?{urlencode({'state': state})}"

    async def exchange_code(self, code: str) -> WeComIdentity:
        try:
            return WeComIdentity(self._codes[code])
        except KeyError as error:
            raise WeComError("WeCom identity exchange failed") from error


def build_wecom_provider(settings: Settings) -> WeComIdentityProvider | FakeWeComIdentityProvider:
    if settings.wecom_fake:
        if settings.environment != "test":
            raise RuntimeError("Fake WeCom mode is permitted only in test")
        return FakeWeComIdentityProvider()
    return WeComIdentityProvider(settings)
