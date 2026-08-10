"""Executable contract for the bounded production-readiness endpoint."""

import asyncio
import json
import os
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Self

import pytest
from fastapi.testclient import TestClient

from superboss.core.config import Settings
from superboss.main import create_app

DEPENDENCY_NAMES = ("postgres", "redis", "minio", "clamav")
SERVER_ROOT = Path(__file__).resolve().parents[2]


async def _ok() -> None:
    return None


def _readiness_contract() -> tuple[type[Any], type[Any]]:
    """Import inside each test so the pre-implementation suite collects as RED."""
    from superboss.modules.health.readiness import ReadinessChecker, ReadinessResult

    return ReadinessChecker, ReadinessResult


def _checker(
    overrides: dict[str, Callable[[], Awaitable[None]]] | None = None,
    *,
    timeout_seconds: float = 0.05,
) -> Any:
    ReadinessChecker, _ = _readiness_contract()
    probes: dict[str, Callable[[], Awaitable[None]]] = {
        dependency: _ok for dependency in DEPENDENCY_NAMES
    }
    probes.update(overrides or {})
    return ReadinessChecker(probes=probes, timeout_seconds=timeout_seconds)


def _test_settings() -> Settings:
    return Settings(
        environment="test",
        database_url="postgresql+asyncpg://unit:unit@127.0.0.1:1/unit",
        jwt_secret="test-only-signing-secret-with-at-least-thirty-two-bytes",
        wecom_fake=True,
        lifecycle_reconcile_interval_seconds=0,
    )


def _client_with(checker: Any) -> TestClient:
    app = create_app(
        _test_settings(),
        object_storage=object(),  # type: ignore[arg-type]
        enqueue_file_scan=lambda _file_id, _delivery_key: None,
    )
    app.state.readiness_checker = checker
    return TestClient(app)


def test_readiness_reports_all_dependencies_ok() -> None:
    response = _client_with(_checker()).get("/api/v1/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "dependencies": {
            "postgres": "ok",
            "redis": "ok",
            "minio": "ok",
            "clamav": "ok",
        },
    }


@pytest.mark.parametrize("failed_dependency", DEPENDENCY_NAMES)
def test_readiness_reports_one_failed_dependency(
    failed_dependency: str,
) -> None:
    async def fail() -> None:
        raise RuntimeError("provider detail must not cross the boundary")

    response = _client_with(_checker({failed_dependency: fail})).get("/api/v1/health/ready")

    expected_dependencies = {dependency: "ok" for dependency in DEPENDENCY_NAMES}
    expected_dependencies[failed_dependency] = "failed"
    assert response.status_code == 503
    assert response.json() == {
        "status": "failed",
        "dependencies": expected_dependencies,
    }


def test_readiness_times_out_each_probe_without_waiting_for_it_forever() -> None:
    async def never_finishes() -> None:
        await asyncio.Event().wait()

    started = time.monotonic()
    response = _client_with(_checker({"redis": never_finishes}, timeout_seconds=0.02)).get(
        "/api/v1/health/ready"
    )
    elapsed = time.monotonic() - started

    assert elapsed < 0.5
    assert response.status_code == 503
    assert response.json()["dependencies"] == {
        "postgres": "ok",
        "redis": "failed",
        "minio": "ok",
        "clamav": "ok",
    }


def test_readiness_exception_is_fixed_and_secret_safe() -> None:
    secret = "postgresql://admin:do-not-leak@database/superboss"

    async def expose_secret_if_unsanitized() -> None:
        raise RuntimeError(secret)

    response = _client_with(_checker({"postgres": expose_secret_if_unsanitized})).get(
        "/api/v1/health/ready"
    )
    rendered = response.text

    assert response.status_code == 503
    assert secret not in rendered
    assert "do-not-leak" not in rendered
    assert set(response.json()) == {"status", "dependencies"}
    assert set(response.json()["dependencies"]) == set(DEPENDENCY_NAMES)


@pytest.mark.asyncio
async def test_readiness_runs_only_the_four_known_probes_concurrently() -> None:
    active = 0
    maximum_active = 0
    all_started = asyncio.Event()
    release = asyncio.Event()

    async def measured_probe() -> None:
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        if active == len(DEPENDENCY_NAMES):
            all_started.set()
        try:
            await release.wait()
        finally:
            active -= 1

    checker = _checker(
        {dependency: measured_probe for dependency in DEPENDENCY_NAMES},
        timeout_seconds=1,
    )
    task = asyncio.create_task(checker.check())
    await asyncio.wait_for(all_started.wait(), timeout=0.2)

    assert maximum_active == 4
    release.set()
    result = await asyncio.wait_for(task, timeout=0.2)
    assert result.dependencies == {dependency: "ok" for dependency in DEPENDENCY_NAMES}


@pytest.mark.asyncio
async def test_readiness_cancellation_propagates_and_cancels_all_probes() -> None:
    started = [asyncio.Event() for _ in DEPENDENCY_NAMES]
    stopped = [asyncio.Event() for _ in DEPENDENCY_NAMES]

    def blocking_probe(index: int) -> Callable[[], Awaitable[None]]:
        async def probe() -> None:
            started[index].set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped[index].set()

        return probe

    checker = _checker(
        {dependency: blocking_probe(index) for index, dependency in enumerate(DEPENDENCY_NAMES)},
        timeout_seconds=1,
    )
    task = asyncio.create_task(checker.check())
    await asyncio.wait_for(asyncio.gather(*(event.wait() for event in started)), timeout=0.2)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.wait_for(asyncio.gather(*(event.wait() for event in stopped)), timeout=0.2)


@pytest.mark.asyncio
async def test_default_probes_use_postgres_redis_minio_and_clamd_protocols() -> None:
    from superboss.modules.health.readiness import build_default_readiness_checker

    observed: dict[str, bytes] = {}

    async def redis_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                prefix = await asyncio.wait_for(reader.readline(), timeout=0.2)
                if not prefix:
                    return
                observed["redis"] = observed.get("redis", b"") + prefix
                if not prefix.startswith(b"*"):
                    command = prefix.strip().upper()
                else:
                    arguments: list[bytes] = []
                    for _ in range(int(prefix[1:-2])):
                        length_line = await asyncio.wait_for(reader.readline(), timeout=0.2)
                        length = int(length_line[1:-2])
                        argument = await asyncio.wait_for(
                            reader.readexactly(length + 2), timeout=0.2
                        )
                        observed["redis"] += length_line + argument
                        arguments.append(argument[:-2])
                    command = arguments[0].upper()
                writer.write(b"+PONG\r\n" if command == b"PING" else b"+OK\r\n")
                await writer.drain()
                if command == b"PING":
                    return
        finally:
            writer.close()
            await writer.wait_closed()

    async def minio_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            observed["minio"] = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=0.2)
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    async def clamav_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            observed["clamav"] = await asyncio.wait_for(
                reader.readexactly(len(b"zPING\0")), timeout=0.2
            )
            writer.write(b"PONG\0")
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    redis_server = await asyncio.start_server(redis_handler, "127.0.0.1", 0)
    minio_server = await asyncio.start_server(minio_handler, "127.0.0.1", 0)
    clamav_server = await asyncio.start_server(clamav_handler, "127.0.0.1", 0)
    redis_port = redis_server.sockets[0].getsockname()[1]
    minio_port = minio_server.sockets[0].getsockname()[1]
    clamav_port = clamav_server.sockets[0].getsockname()[1]

    class Connection:
        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def execute(self, statement: object) -> None:
            observed["postgres"] = str(statement).encode("ascii")

    class Engine:
        def connect(self) -> Connection:
            return Connection()

    settings = _test_settings()
    settings.redis_url = f"redis://health:probe-secret@127.0.0.1:{redis_port}/0"
    settings.s3_endpoint_url = f"http://127.0.0.1:{minio_port}"
    settings.clamav_host = "127.0.0.1"
    settings.clamav_port = clamav_port
    checker = build_default_readiness_checker(
        settings=settings,
        engine=Engine(),
        timeout_seconds=0.5,
    )
    try:
        result = await checker.check()
    finally:
        for server in (redis_server, minio_server, clamav_server):
            server.close()
        await asyncio.gather(
            redis_server.wait_closed(),
            minio_server.wait_closed(),
            clamav_server.wait_closed(),
        )

    assert result.dependencies == {dependency: "ok" for dependency in DEPENDENCY_NAMES}
    assert observed["postgres"].upper() == b"SELECT 1"
    assert b"AUTH" in observed["redis"]
    assert b"probe-secret" in observed["redis"]
    assert b"PING" in observed["redis"]
    minio_request_line = observed["minio"].split(b"\r\n", maxsplit=1)[0]
    assert minio_request_line == b"GET /minio/health/ready HTTP/1.1"
    assert observed["clamav"] == b"zPING\0"


def test_readiness_module_import_and_app_construction_open_no_network() -> None:
    script = """
import asyncio
import json
import socket

def deny(*_args, **_kwargs):
    raise AssertionError("network attempted during import or construction")

socket.socket.connect = deny
socket.socket.connect_ex = deny
socket.create_connection = deny
asyncio.open_connection = deny

from superboss.core.config import Settings
from superboss.main import create_app
from superboss.modules.health.readiness import ReadinessChecker

settings = Settings(
    environment="test",
    database_url="postgresql+asyncpg://unit:unit@127.0.0.1:1/unit",
    jwt_secret="test-only-signing-secret-with-at-least-thirty-two-bytes",
    wecom_fake=True,
    lifecycle_reconcile_interval_seconds=0,
)
app = create_app(
    settings,
    object_storage=object(),
    enqueue_file_scan=lambda _file_id, _delivery_key: None,
)
print(json.dumps({"title": app.title, "checker": ReadinessChecker.__name__}))
"""
    environment = os.environ.copy()
    environment.pop("HTTP_PROXY", None)
    environment.pop("HTTPS_PROXY", None)
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=SERVER_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "title": "SuperBoss API",
        "checker": "ReadinessChecker",
    }
