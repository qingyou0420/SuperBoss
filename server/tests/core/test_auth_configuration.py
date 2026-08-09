"""Fail-fast signing configuration behavior."""

import secrets

import pytest

from superboss.core.config import Settings
from superboss.main import create_app


@pytest.mark.parametrize("secret", ["", "x" * 31, "x" * 32, "password" * 4, "CHANGE_ME_" * 4, "abcdefgh" * 4, "12345678" * 4, "0123456789abcdef" * 2])
def test_staging_and_production_reject_weak_jwt_keys(secret: str) -> None:
    """Removing startup validation would make weak signing keys boot successfully."""
    for environment in ("staging", "production"):
        with pytest.raises(ValueError, match="JWT secret"):
            create_app(Settings(environment=environment, jwt_secret=secret))


def test_generated_base64url_key_is_accepted_in_deployment_environments() -> None:
    """Rejecting a secret-manager-compatible 32-byte key would block secure deployment."""
    key = secrets.token_urlsafe(32)
    for environment in ("staging", "production"):
        assert Settings(environment=environment, jwt_secret=key).jwt_secret == key
