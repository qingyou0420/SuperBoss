from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    environment: str = "development"

    model_config = SettingsConfigDict(env_prefix="SUPERBOSS_", extra="forbid")


@lru_cache
def get_settings() -> Settings:
    return Settings()
