"""Bounded, secret-safe readiness probes for production dependencies."""

import asyncio
import logging
import ssl
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from superboss.core.config import Settings

DEPENDENCY_NAMES = ("postgres", "redis", "minio", "clamav")
DependencyProbe = Callable[[], Awaitable[None]]
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReadinessResult:
    dependencies: dict[str, str]

    @property
    def status(self) -> str:
        return "ok" if all(value == "ok" for value in self.dependencies.values()) else "failed"


class ReadinessChecker:
    def __init__(
        self,
        *,
        probes: Mapping[str, DependencyProbe],
        timeout_seconds: float,
    ) -> None:
        if set(probes) != set(DEPENDENCY_NAMES):
            raise ValueError("readiness probes must contain the four production dependencies")
        if timeout_seconds <= 0:
            raise ValueError("readiness timeout must be positive")
        self._probes = {name: probes[name] for name in DEPENDENCY_NAMES}
        self._timeout_seconds = timeout_seconds

    async def _check_one(self, probe: DependencyProbe) -> str:
        try:
            async with asyncio.timeout(self._timeout_seconds):
                await probe()
        except Exception:  # noqa: BLE001 - dependency details never cross this boundary
            return "failed"
        return "ok"

    async def check(self) -> ReadinessResult:
        tasks = {
            name: asyncio.create_task(self._check_one(probe))
            for name, probe in self._probes.items()
        }
        try:
            statuses = await asyncio.gather(*(tasks[name] for name in DEPENDENCY_NAMES))
        except BaseException:
            for task in tasks.values():
                task.cancel()
            await asyncio.gather(*tasks.values(), return_exceptions=True)
            raise
        return ReadinessResult(dict(zip(DEPENDENCY_NAMES, statuses, strict=True)))


async def _probe_postgres(engine: AsyncEngine) -> None:
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))


def _redis_frame(*arguments: str) -> bytes:
    encoded = [argument.encode("utf-8") for argument in arguments]
    chunks = [f"*{len(encoded)}\r\n".encode("ascii")]
    for argument in encoded:
        chunks.extend((f"${len(argument)}\r\n".encode("ascii"), argument, b"\r\n"))
    return b"".join(chunks)


async def _redis_command(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *arguments: str,
) -> bytes:
    writer.write(_redis_frame(*arguments))
    await writer.drain()
    response = await reader.readuntil(b"\r\n")
    if len(response) > 1024 or not response.startswith(b"+"):
        raise RuntimeError("redis readiness failed")
    return response[1:-2]


async def _probe_redis(redis_url: str) -> None:
    writer: asyncio.StreamWriter | None = None
    try:
        parsed = urlsplit(redis_url)
        if parsed.scheme not in {"redis", "rediss"} or parsed.hostname is None:
            raise RuntimeError("redis readiness failed")
        use_tls = parsed.scheme == "rediss"
        tls_context = ssl.create_default_context() if use_tls else None
        reader, writer = await asyncio.open_connection(
            parsed.hostname,
            parsed.port or (6380 if use_tls else 6379),
            ssl=tls_context,
            server_hostname=parsed.hostname if use_tls else None,
        )
        username = unquote(parsed.username) if parsed.username else None
        password = unquote(parsed.password) if parsed.password is not None else None
        if password is not None:
            auth = ("AUTH", username, password) if username is not None else ("AUTH", password)
            if await _redis_command(reader, writer, *auth) != b"OK":
                raise RuntimeError("redis readiness failed")
        database = parsed.path.removeprefix("/") or "0"
        if not database.isdecimal():
            raise RuntimeError("redis readiness failed")
        if database != "0" and await _redis_command(reader, writer, "SELECT", database) != b"OK":
            raise RuntimeError("redis readiness failed")
        if await _redis_command(reader, writer, "PING") != b"PONG":
            raise RuntimeError("redis readiness failed")
    finally:
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception as error:  # noqa: BLE001
                logger.debug("redis readiness socket close failed: %s", type(error).__name__)


async def _probe_minio(endpoint_url: str) -> None:
    endpoint = endpoint_url.rstrip("/") + "/minio/health/ready"
    async with httpx.AsyncClient(follow_redirects=False, trust_env=False) as client:
        response = await client.get(endpoint)
    if response.status_code != 200:
        raise RuntimeError("minio readiness failed")


async def _probe_clamav(host: str, port: int) -> None:
    writer: asyncio.StreamWriter | None = None
    try:
        reader, writer = await asyncio.open_connection(host, port)
        writer.write(b"zPING\0")
        await writer.drain()
        if await reader.readexactly(len(b"PONG\0")) != b"PONG\0":
            raise RuntimeError("clamav readiness failed")
    finally:
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception as error:  # noqa: BLE001
                logger.debug("clamav readiness socket close failed: %s", type(error).__name__)


def build_default_readiness_checker(
    *,
    settings: Settings,
    engine: AsyncEngine,
    timeout_seconds: float = 3.0,
) -> ReadinessChecker:
    return ReadinessChecker(
        probes={
            "postgres": lambda: _probe_postgres(engine),
            "redis": lambda: _probe_redis(settings.redis_url),
            "minio": lambda: _probe_minio(settings.s3_endpoint_url),
            "clamav": lambda: _probe_clamav(settings.clamav_host, settings.clamav_port),
        },
        timeout_seconds=timeout_seconds,
    )
