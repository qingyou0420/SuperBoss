"""Application wiring for durable file lifecycle maintenance."""

import threading

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


def test_lifespan_reconciles_retries_and_disposes_engine(monkeypatch) -> None:
    """A failed maintenance iteration is safe and does not stop the next bounded iteration."""
    from superboss import main

    first = threading.Event()
    second = threading.Event()
    calls = 0

    class RecordingLifecycle:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def reconcile(self, _limit: int) -> int:
            nonlocal calls
            calls += 1
            (first if calls == 1 else second).set()
            if calls == 1:
                raise RuntimeError("provider secret")
            return 0

    monkeypatch.setattr(main, "FileLifecycleService", RecordingLifecycle)
    settings = _settings(
        lifecycle_reconcile_interval_seconds=0.01,
        lifecycle_reconcile_batch_size=1,
    )
    app = create_app(settings, object_storage=InMemoryObjectStorage())
    with TestClient(app):
        assert first.wait(1) and second.wait(1)
    assert app.state.lifecycle_maintenance_task.done()
