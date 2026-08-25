"""Application wiring without constructing live cloud clients."""

from fastapi.testclient import TestClient

from superboss.core.config import Settings
from superboss.main import create_app
from tests.files.storage import InMemoryObjectStorage


def _settings(**updates: object) -> Settings:
    return Settings(
        environment="test",
        database_url="postgresql+asyncpg://postgres@127.0.0.1:55432/superboss_task3",
        jwt_secret="test-only-signing-secret-with-at-least-thirty-two-bytes",
        **updates,
    )


def test_injected_file_boundaries_do_not_construct_boto_client(monkeypatch) -> None:
    """Tests and embedders can supply storage without any boto construction side effect."""
    from superboss import main

    def fail_boto(*_args, **_kwargs):
        raise AssertionError("boto must not be constructed")

    monkeypatch.setattr(main, "Boto3ObjectStorage", fail_boto)
    storage = InMemoryObjectStorage()
    dispatcher = lambda _file_id, _delivery_key: None
    app = create_app(_settings(), object_storage=storage, enqueue_file_scan=dispatcher)
    assert app.state.object_storage is storage
    assert app.state.enqueue_file_scan is dispatcher
    with TestClient(app):
        pass
