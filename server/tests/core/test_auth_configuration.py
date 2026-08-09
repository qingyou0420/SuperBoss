"""Fail-fast signing configuration behavior."""

import pytest

from superboss.core.config import Settings
from superboss.main import create_app


@pytest.mark.parametrize("secret", ["", "x" * 31])
def test_staging_and_production_reject_weak_jwt_keys(secret: str) -> None:
    """Removing startup validation would make weak signing keys boot successfully."""
    for environment in ("staging", "production"):
        with pytest.raises(ValueError, match="JWT secret"):
            create_app(Settings(environment=environment, jwt_secret=secret))
