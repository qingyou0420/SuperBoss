"""Default scan-dispatcher safety behavior."""

from uuid import uuid4

import pytest

from superboss.core.config import Settings
from superboss.main import create_app


def test_default_file_scan_dispatcher_fails_closed_without_secret_details(
    test_settings: Settings,
) -> None:
    """A silent default would let completed uploads remain permanently unscanned."""
    app = create_app(test_settings)

    with pytest.raises(RuntimeError, match="file scan dispatcher is not configured") as error:
        app.state.enqueue_file_scan(uuid4())

    assert "secret" not in str(error.value).lower()
