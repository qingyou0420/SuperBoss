"""Fail-fast signing configuration behavior."""

import base64
import secrets

import pytest

from superboss.core.config import Settings
from superboss.main import create_app

PUBLIC_S3_ORIGIN = "https://objects.nightforest.com"


@pytest.mark.parametrize("secret", ["", "x" * 31, "x" * 32, "password" * 4, "CHANGE_ME_" * 4, "abcdefgh" * 4, "12345678" * 4, "0123456789abcdef" * 2])
def test_staging_and_production_reject_weak_jwt_keys(secret: str) -> None:
    """Removing startup validation would make weak signing keys boot successfully."""
    for environment in ("staging", "production"):
        with pytest.raises(ValueError, match="JWT secret"):
            create_app(
                Settings(
                    environment=environment,
                    jwt_secret=secret,
                    s3_public_endpoint_url=PUBLIC_S3_ORIGIN,
                )
            )


def test_generated_base64url_key_is_accepted_in_deployment_environments() -> None:
    """Rejecting a secret-manager-compatible 32-byte key would block secure deployment."""
    key = secrets.token_urlsafe(32)
    for environment in ("staging", "production"):
        assert (
            Settings(
                environment=environment,
                jwt_secret=key,
                s3_public_endpoint_url=PUBLIC_S3_ORIGIN,
            ).jwt_secret
            == key
        )


@pytest.mark.parametrize(
    "secret",
    [
        "example-jwt-signing-key-for-production-change-me-now",
        base64.b64encode(bytes(range(32))).decode("ascii"),
        f"{secrets.token_urlsafe(32)[:10]} {secrets.token_urlsafe(32)[10:]}",
        f"{secrets.token_urlsafe(32)}===",
        "change_me_abcdefghijklmnopqrstuvwxyz0123456789_-ABCDE",
        "this-is-a-common-jwt-key-for-production-use-only-1234567890X",
        "this-is-a-secret-jwt-key-for-production-use-only-1234567890X",
    ],
)
def test_deployment_keys_must_be_strict_unpadded_canonical_base64url(secret: str) -> None:
    """Accepting alternate encodings or placeholders defeats the deployable secret contract."""
    for environment in ("staging", "production"):
        with pytest.raises(ValueError, match="canonical base64url"):
            Settings(
                environment=environment,
                jwt_secret=secret,
                s3_public_endpoint_url=PUBLIC_S3_ORIGIN,
            )


def test_deployment_key_validation_does_not_render_candidate_input() -> None:
    """Configuration errors must not leak a rejected signing key into logs or UI."""
    candidate = "this-is-a-common-key-PRIVATEKEYTAIL987654321-abcdefghijklmnop"
    with pytest.raises(ValueError) as error:
        Settings(
            environment="production",
            jwt_secret=candidate,
            s3_public_endpoint_url=PUBLIC_S3_ORIGIN,
        )
    rendered = str(error.value)
    assert candidate not in rendered
    assert "PRIVATEKEYTAIL987654321" not in rendered
    assert "input_value" not in rendered
