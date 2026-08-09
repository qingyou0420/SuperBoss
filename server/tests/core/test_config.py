import pytest
from pydantic import ValidationError

from superboss.core.config import Settings, get_settings


def test_settings_reads_environment_from_superboss_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SUPERBOSS_ENVIRONMENT", "production")

    assert Settings().environment == "production"


def test_settings_rejects_unknown_initialization_fields() -> None:
    with pytest.raises(ValidationError):
        Settings(unrecognized_setting=True)


def test_get_settings_returns_the_cached_settings_instance() -> None:
    get_settings.cache_clear()

    assert get_settings() is get_settings()
