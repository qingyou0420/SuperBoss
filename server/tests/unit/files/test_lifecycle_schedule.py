"""Production scheduling contract for durable file lifecycle reconciliation."""

import importlib
import socket
import sys
from types import SimpleNamespace
from typing import Any

import pytest


def _contract() -> tuple[Any, Any]:
    schedules = importlib.import_module("superboss.workers.schedules")
    celery_module = importlib.import_module("superboss.workers.celery_app")
    return schedules, celery_module.celery_app


def test_lifecycle_reconcile_task_has_minute_schedule_and_delivery_safety() -> None:
    schedules, app = _contract()
    task = schedules.reconcile_file_lifecycle_task
    entry = app.conf.beat_schedule["reconcile-file-lifecycle-every-minute"]

    assert task.name == "superboss.files.reconcile_lifecycle"
    assert task.acks_late is True and task.reject_on_worker_lost is True
    assert task.max_retries == 3 and task.retry_backoff is True
    assert 0 < task.soft_time_limit < task.time_limit
    assert entry == {
        "task": task.name,
        "schedule": 60.0,
        "options": {"queue": "file-maintenance"},
    }
    assert app.conf.task_routes[task.name]["queue"] == "file-maintenance"


def test_lifecycle_reconcile_task_drives_async_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    schedules, _app = _contract()
    seen: list[bool] = []

    async def execute() -> int:
        seen.append(True)
        return 11

    monkeypatch.setattr(schedules, "_run_lifecycle_reconcile", execute)

    assert schedules.reconcile_file_lifecycle_task.run() == 11
    assert seen == [True]


@pytest.mark.asyncio
async def test_execution_layer_awaits_full_lifecycle_reconcile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schedules, _app = _contract()
    factory = object()
    storage = object()
    dispatcher = object()
    seen: list[tuple[object, object, object, int]] = []

    class FakeLifecycleService:
        def __init__(
            self,
            received_factory: object,
            received_storage: object,
            received_dispatcher: object,
        ) -> None:
            seen.append((received_factory, received_storage, received_dispatcher, -1))

        async def reconcile(self, limit: int) -> int:
            seen[-1] = (*seen[-1][:3], limit)
            return 13

    monkeypatch.setattr(schedules, "FileLifecycleService", FakeLifecycleService)

    result = await schedules.execute_lifecycle_reconcile(
        session_factory=factory,
        storage=storage,
        enqueue_scan=dispatcher,
        limit=100,
    )

    assert result == 13
    assert seen == [(factory, storage, dispatcher, 100)]


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_fails", [False, True])
async def test_production_runner_constructs_dependencies_and_always_disposes_engine(
    monkeypatch: pytest.MonkeyPatch,
    provider_fails: bool,
) -> None:
    schedules, _app = _contract()
    settings = SimpleNamespace(
        database_url="postgresql+asyncpg://local/test",
        s3_bucket="files",
        s3_endpoint_url="http://minio:9000",
        s3_access_key_id="access",
        s3_secret_access_key="secret",
    )
    created: dict[str, object] = {}

    class FakeEngine:
        disposed = False

        async def dispose(self) -> None:
            self.disposed = True

    engine = FakeEngine()
    factory = object()
    storage = object()

    def build_engine(url: str, *, pool_pre_ping: bool) -> FakeEngine:
        created["engine"] = (url, pool_pre_ping)
        return engine

    def build_factory(received_engine: object, *, expire_on_commit: bool) -> object:
        created["factory"] = (received_engine, expire_on_commit)
        return factory

    def build_storage(*args: object) -> object:
        created["storage"] = args
        return storage

    async def execute(**kwargs: object) -> int:
        created["execute"] = kwargs
        if provider_fails:
            raise RuntimeError("provider failed")
        return 17

    monkeypatch.setattr(schedules, "get_settings", lambda: settings)
    monkeypatch.setattr(schedules, "create_async_engine", build_engine)
    monkeypatch.setattr(schedules, "async_sessionmaker", build_factory)
    monkeypatch.setattr(schedules, "Boto3ObjectStorage", build_storage)
    monkeypatch.setattr(schedules, "execute_lifecycle_reconcile", execute)

    if provider_fails:
        with pytest.raises(RuntimeError, match="provider failed"):
            await schedules._run_lifecycle_reconcile()
    else:
        assert await schedules._run_lifecycle_reconcile() == 17

    assert created == {
        "engine": (settings.database_url, True),
        "factory": (engine, False),
        "storage": (
            settings.s3_bucket,
            settings.s3_endpoint_url,
            settings.s3_access_key_id,
            settings.s3_secret_access_key,
        ),
        "execute": {
            "session_factory": factory,
            "storage": storage,
            "enqueue_scan": schedules.enqueue_file_scan,
            "limit": 100,
        },
    }
    assert engine.disposed is True


def test_schedule_module_import_opens_no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    attempted: list[object] = []

    def forbid_connect(sock: socket.socket, address: object) -> None:
        attempted.append((sock, address))
        raise AssertionError("network connection attempted")

    monkeypatch.setattr(socket.socket, "connect", forbid_connect)
    sys.modules.pop("superboss.workers.schedules", None)

    importlib.import_module("superboss.workers.schedules")

    assert attempted == []
