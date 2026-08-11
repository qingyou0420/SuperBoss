import base64
import re
from functools import lru_cache
from urllib.parse import urlsplit

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    environment: str = "development"
    database_url: str = "postgresql+asyncpg://superboss:CHANGE_ME@localhost:5432/superboss"
    jwt_secret: str = ""
    s3_bucket: str = "superboss-files"
    s3_endpoint_url: str = "http://127.0.0.1:9000"
    s3_public_endpoint_url: str | None = None
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    redis_url: str = "redis://127.0.0.1:6379/0"
    clamav_host: str = "127.0.0.1"
    clamav_port: int = 3310
    clamav_connect_timeout_seconds: float = 3.0
    clamav_io_timeout_seconds: float = 10.0
    clamav_total_timeout_seconds: float = 600.0
    clamav_max_chunk_bytes: int = 1024 * 1024
    clamav_max_stream_bytes: int = 100 * 1024 * 1024
    clamav_max_response_bytes: int = 1024
    scan_soft_time_limit_seconds: int = 660
    scan_hard_time_limit_seconds: int = 720
    lifecycle_reconcile_interval_seconds: float = 30.0
    lifecycle_reconcile_batch_size: int = 100

    model_config = SettingsConfigDict(env_prefix="SUPERBOSS_", extra="forbid", hide_input_in_errors=True)

    @model_validator(mode="after")
    def validate_secure_jwt_secret(self) -> "Settings":
        public_endpoint = self.s3_public_endpoint_url
        if self.environment == "production" and not public_endpoint:
            raise ValueError("public S3 endpoint must be an HTTPS origin")
        if public_endpoint is not None:
            try:
                parsed_public_endpoint = urlsplit(public_endpoint)
                public_port = parsed_public_endpoint.port
            except ValueError:
                raise ValueError("public S3 endpoint must be an HTTPS origin") from None
            public_hostname = parsed_public_endpoint.hostname
            hostname_labels = public_hostname.split(".") if public_hostname is not None else []
            canonical_hostname = bool(hostname_labels) and len(public_hostname or "") <= 253 and all(
                re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                for label in hostname_labels
            )
            canonical_authority = public_hostname or ""
            if public_port is not None:
                canonical_authority = f"{canonical_authority}:{public_port}"
            if (
                parsed_public_endpoint.scheme != "https"
                or not canonical_hostname
                or parsed_public_endpoint.username is not None
                or parsed_public_endpoint.password is not None
                or parsed_public_endpoint.path
                or parsed_public_endpoint.query
                or parsed_public_endpoint.fragment
                or public_port in {0, 443}
                or public_endpoint != f"https://{canonical_authority}"
            ):
                raise ValueError("public S3 endpoint must be an HTTPS origin")
        if self.lifecycle_reconcile_interval_seconds < 0:
            raise ValueError("Lifecycle reconcile interval must be non-negative")
        if self.environment != "test" and self.lifecycle_reconcile_interval_seconds <= 0:
            raise ValueError("Lifecycle reconcile interval must be positive")
        if self.lifecycle_reconcile_batch_size < 1:
            raise ValueError("Lifecycle reconcile batch size must be positive")
        if not self.redis_url.startswith(("redis://", "rediss://")):
            raise ValueError("Redis URL must use the Redis scheme")
        if not self.clamav_host or not 1 <= self.clamav_port <= 65535:
            raise ValueError("ClamAV endpoint is invalid")
        if (
            self.clamav_connect_timeout_seconds <= 0
            or self.clamav_io_timeout_seconds <= 0
            or self.clamav_total_timeout_seconds <= 0
            or not 1 <= self.clamav_max_chunk_bytes <= self.clamav_max_stream_bytes
            or self.clamav_max_stream_bytes > 100 * 1024 * 1024
            or self.clamav_max_response_bytes < 1
        ):
            raise ValueError("ClamAV scan limits are invalid")
        if not (
            self.clamav_total_timeout_seconds
            < self.scan_soft_time_limit_seconds
            < self.scan_hard_time_limit_seconds
            < 24 * 60 * 60
        ):
            raise ValueError("Scan task time limits are invalid")
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
