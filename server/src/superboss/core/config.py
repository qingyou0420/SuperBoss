import base64
import re
from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    environment: str = "development"
    database_url: str = "postgresql+asyncpg://superboss:CHANGE_ME@localhost:5432/superboss"
    jwt_secret: str = ""
    wecom_corp_id: str = ""
    wecom_agent_id: str = ""
    wecom_corp_secret: str = ""
    wecom_redirect_uri: str = ""
    wecom_fake: bool = False
    owner_wecom_userid: str = ""

    model_config = SettingsConfigDict(env_prefix="SUPERBOSS_", extra="forbid", hide_input_in_errors=True)

    @model_validator(mode="after")
    def validate_secure_jwt_secret(self) -> "Settings":
        if self.environment in {"staging", "production"}:
            candidate = self.jwt_secret
            try:
                if not re.fullmatch(r"[A-Za-z0-9_-]+", candidate):
                    raise ValueError
                padded = candidate + "=" * (-len(candidate) % 4)
                material = base64.urlsafe_b64decode(padded.encode("ascii"))
            except (UnicodeEncodeError, ValueError):
                raise ValueError("JWT secret must be canonical base64url random material") from None
            if base64.urlsafe_b64encode(material).rstrip(b"=").decode("ascii") != candidate:
                raise ValueError("JWT secret must be canonical base64url random material")
            periodic = any(material == material[:period] * (len(material) // period) for period in range(1, len(material) // 2 + 1) if len(material) % period == 0)
            markers = (
                "change-me", "change_me", "changeme", "example", "placeholder", "password", "common", "secret",
            )
            if len(material) < 32 or periodic or len(set(material)) < 16 or any(marker in candidate.lower() for marker in markers):
                raise ValueError("JWT secret must be canonical base64url random material")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
