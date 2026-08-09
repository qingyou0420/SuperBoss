import base64
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

    model_config = SettingsConfigDict(env_prefix="SUPERBOSS_", extra="forbid")

    @model_validator(mode="after")
    def validate_secure_jwt_secret(self) -> "Settings":
        if self.environment in {"staging", "production"}:
            try:
                padded = self.jwt_secret + "=" * (-len(self.jwt_secret) % 4)
                material = base64.urlsafe_b64decode(padded.encode("ascii"))
            except (UnicodeEncodeError, ValueError):
                raise ValueError("JWT secret must be canonical base64url random material") from None
            periodic = any(material == material[:period] * (len(material) // period) for period in range(1, len(material) // 2 + 1) if len(material) % period == 0)
            if len(material) < 32 or periodic or len(set(material)) < 16:
                raise ValueError("JWT secret must be canonical base64url random material")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
