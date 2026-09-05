"""Celery scan delivery and execution contracts without live broker access."""

import importlib
import socket
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from superboss.modules.files.models import File, FileState
from tests.files.factory import add_folder
from tests.identity import local_user


def celery_contract() -> tuple[Any, Any]:
    tasks = importlib.import_module("superboss.modules.files.tasks")
    celery_module = importlib.import_module("superboss.workers.celery_app")
    return tasks, celery_module.celery_app


def test_scan_task_has_stable_delivery_and_worker_limits() -> None:
    """Changing task identity or delivery limits would break outbox and worker safety."""
    tasks, app = celery_contract()
    task = tasks.scan_file_task

    assert task.name == "superboss.files.scan"
    assert task.acks_late is True
    assert task.reject_on_worker_lost is True
    assert task.max_retries == 3
    assert task.retry_backoff is True
    assert 0 < task.soft_time_limit < task.time_limit < 24 * 60 * 60
    assert app.conf.worker_prefetch_multiplier == 1
    assert app.conf.worker_concurrency == 1
    assert app.conf.task_reject_on_worker_lost is True
    assert app.conf.task_default_queue == "file-scan"
    assert app.conf.task_routes[task.name]["queue"] == "file-scan"
    assert str(app.conf.broker_url).startswith("redis://")


def test_dispatcher_maps_stable_delivery_key_without_live_broker(monkeypatch) -> None:
    """Losing the outbox delivery key would permit duplicate queue identities."""
    tasks, _app = celery_contract()
    file_id = uuid4()
    delivery_key = uuid4()
    sent: list[dict[str, object]] = []

    def apply_async(**options: object) -> None:
        sent.append(options)

    monkeypatch.setattr(tasks.scan_file_task, "apply_async", apply_async)

    tasks.enqueue_file_scan(file_id, delivery_key)

    assert sent == [
        {
            "args": [str(file_id)],
            "task_id": str(delivery_key),
            "queue": "file-scan",
        }
    ]


def test_dispatcher_propagates_broker_failure_for_outbox_retry(monkeypatch) -> None:
    """Swallowing publish failure would falsely mark durable outbox work delivered."""
    tasks, _app = celery_contract()

    def fail_publish(**_options: object) -> None:
        raise ConnectionError("redis provider secret")

    monkeypatch.setattr(tasks.scan_file_task, "apply_async", fail_publish)

    with pytest.raises(ConnectionError, match="redis provider secret"):
        tasks.enqueue_file_scan(uuid4(), uuid4())


def test_task_run_awaits_async_scan_execution(monkeypatch) -> None:
    """Forgetting to drive the async service would acknowledge an unscanned task."""
    tasks, _app = celery_contract()
    file_id = uuid4()
    seen: list[str] = []

    async def execute(value: str) -> None:
        seen.append(value)

    monkeypatch.setattr(tasks, "_run_scan_file", execute)

    tasks.scan_file_task.run(str(file_id))

    assert seen == [str(file_id)]


@pytest.mark.asyncio
async def test_execution_layer_preserves_terminal_replay_noop(
    db_session: AsyncSession,
) -> None:
    """Task wiring must retain FileScanService's no-I/O terminal replay contract."""
    tasks, _app = celery_contract()
    owner = local_user("celery-scan", display_name="Celery")
    db_session.add(owner)
    await db_session.flush()
    folder = await add_folder(db_session, owner.id)
    file = File(
        folder_id=folder.id,
        filename="clean.pdf",
        object_key=f"folders/{folder.id}/docs/clean.pdf",
        size_bytes=1,
        sha256="0" * 64,
        state=FileState.CLEAN,
        uploader_id=owner.id,
        content_type="application/pdf",
        scan_result="CLEAN",
    )
    db_session.add(file)
    await db_session.commit()
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    class NoReplayStorage:
        calls = 0

        def stream(self, _object_key: str) -> AsyncIterator[bytes]:
            self.calls += 1

            async def content() -> AsyncIterator[bytes]:
                yield b"unreachable"

            return content()

    class NoReplayScanner:
        calls = 0

        async def scan(self, _chunks: AsyncIterator[bytes]) -> object:
            self.calls += 1
            raise AssertionError("terminal replay reached scanner")

    storage = NoReplayStorage()
    scanner = NoReplayScanner()
    await tasks.execute_file_scan(
        str(file.id),
        session_factory=factory,
        storage=storage,
        scanner=scanner,
    )

    assert storage.calls == 0
    assert scanner.calls == 0


def test_import_and_injected_app_construction_open_no_network(monkeypatch, test_settings) -> None:
    """Importing workers or constructing an injected API must not dial dependencies."""
    attempted: list[object] = []

    def forbid_connect(sock: socket.socket, address: object) -> None:
        attempted.append(address)
        raise AssertionError("network connection attempted")

    monkeypatch.setattr(socket.socket, "connect", forbid_connect)
    tasks, app = celery_contract()
    from superboss.main import create_app

    storage = object()
    api = create_app(
        test_settings,
        object_storage=storage,
        enqueue_file_scan=lambda _file_id, _delivery_key: None,
    )

    assert tasks.scan_file_task.app is app
    assert api.state.object_storage is storage
    assert attempted == []
