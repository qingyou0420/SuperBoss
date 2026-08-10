import pytest
from pydantic import ValidationError

from superboss.core.config import Settings, get_settings

VALID_DEPLOYMENT_JWT = "ZmFrZS1wcm9kdWN0aW9uLWtleS1tYXRlcmlhbC0zMi1ieXRlcy0xMjM0NTY"


def test_settings_reads_environment_from_superboss_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SUPERBOSS_ENVIRONMENT", "production")
    monkeypatch.setenv("SUPERBOSS_JWT_SECRET", VALID_DEPLOYMENT_JWT)
    monkeypatch.setenv(
        "SUPERBOSS_S3_PUBLIC_ENDPOINT_URL", "https://objects.nightforest.com"
    )

    assert Settings().environment == "production"


def test_settings_rejects_unknown_initialization_fields() -> None:
    with pytest.raises(ValidationError):
        Settings(unrecognized_setting=True)


def test_get_settings_returns_the_cached_settings_instance() -> None:
    get_settings.cache_clear()

    assert get_settings() is get_settings()


def test_development_uses_the_internal_s3_endpoint_for_presigning_by_default() -> None:
    settings = Settings(environment="development")

    assert settings.s3_endpoint_url == "http://127.0.0.1:9000"
    assert settings.s3_public_endpoint_url is None


@pytest.mark.parametrize(
    "endpoint",
    [
        "",
        "http://objects.nightforest.com",
        "https://user@objects.nightforest.com",
        "https://objects.nightforest.com/",
        "https://objects.nightforest.com/path",
        "https://objects.nightforest.com?query=yes",
        "https://objects.nightforest.com#fragment",
        "https://objects .nightforest.com",
        "https://objects.nightforest.com:0",
        "HTTPS://objects.nightforest.com",
        "https://OBJECTS.nightforest.com",
        "https://objects.nightforest.com:443",
        "https://objects.nightforest.com.",
    ],
)
def test_production_requires_a_canonical_https_public_s3_origin(endpoint: str) -> None:
    with pytest.raises(ValueError, match="public S3 endpoint") as error:
        Settings(
            environment="production",
            jwt_secret=VALID_DEPLOYMENT_JWT,
            s3_public_endpoint_url=endpoint,
        )
    if endpoint:
        assert endpoint not in str(error.value)


def test_production_accepts_a_canonical_https_public_s3_origin() -> None:
    settings = Settings(
        environment="production",
        jwt_secret=VALID_DEPLOYMENT_JWT,
        s3_public_endpoint_url="https://objects.nightforest.com",
    )

    assert settings.s3_public_endpoint_url == "https://objects.nightforest.com"
