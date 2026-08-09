from functools import lru_cache

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
