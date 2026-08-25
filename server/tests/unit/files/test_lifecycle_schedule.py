"""Hourly stale-upload recovery schedule and production runner wiring."""

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


def test_stale_recovery_task_has_hourly_schedule_and_delivery_safety() -> None:
    schedules, app = _contract()
    task = schedules.recover_stale_uploads_task
    entry = app.conf.beat_schedule["recover-stale-uploads-hourly"]

    assert task.name == "superboss.files.recover_stale_uploads"
    assert task.acks_late is True and task.reject_on_worker_lost is True
    assert task.max_retries == 3 and task.retry_backoff is True
    assert 0 < task.soft_time_limit < task.time_limit
    assert entry == {
        "task": task.name,
        "schedule": 3600.0,
        "options": {"queue": "file-scan"},
    }
    assert app.conf.task_routes[task.name]["queue"] == "file-scan"


def test_stale_recovery_task_drives_async_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    schedules, _app = _contract()
    seen: list[bool] = []

    async def execute() -> int:
        seen.append(True)
        return 11

    monkeypatch.setattr(schedules, "_run_stale_upload_recovery", execute)

    assert schedules.recover_stale_uploads_task.run() == 11
    assert seen == [True]


@pytest.mark.asyncio
async def test_execution_layer_awaits_stale_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schedules, _app = _contract()
    factory = object()
    storage = object()
    seen: list[tuple[object, object, object]] = []

    class FakeStaleUploadService:
        def __init__(
            self,
            received_factory: object,
            received_storage: object,
            received_dispatcher: object,
        ) -> None:
            seen.append((received_factory, received_storage, received_dispatcher))

        async def recover_stale_uploads(
            self, *, now: object = None, limit: int = 100
        ) -> int:
            del now, limit
            return 13

    monkeypatch.setattr(schedules, "StaleUploadService", FakeStaleUploadService)

    result = await schedules.execute_stale_upload_recovery(
        session_factory=factory,
        storage=storage,
    )

    assert result == 13
    assert seen == [(factory, storage, schedules.enqueue_file_scan)]


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
        s3_public_endpoint_url="https://objects.nightforest.com",
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

    def build_storage(*args: object, **kwargs: object) -> object:
        created["storage"] = (args, kwargs)
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
    monkeypatch.setattr(schedules, "execute_stale_upload_recovery", execute)

    if provider_fails:
        with pytest.raises(RuntimeError, match="provider failed"):
            await schedules._run_stale_upload_recovery()
    else:
        assert await schedules._run_stale_upload_recovery() == 17

    assert created == {
        "engine": (settings.database_url, True),
        "factory": (engine, False),
        "storage": (
            (
                settings.s3_bucket,
                settings.s3_endpoint_url,
                settings.s3_access_key_id,
                settings.s3_secret_access_key,
            ),
            {"public_endpoint_url": settings.s3_public_endpoint_url},
        ),
        "execute": {
            "session_factory": factory,
            "storage": storage,
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
