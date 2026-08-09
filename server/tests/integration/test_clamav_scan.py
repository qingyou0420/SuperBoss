"""Deterministic local-TCP coverage of clamd's bounded INSTREAM protocol."""

import asyncio
import struct
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import pytest


def clamav_contract() -> tuple[type[Any], type[Exception], Any, Any]:
    from superboss.infrastructure.clamav import (
        ClamAVScanError,
        ClamAVScanner,
        ScanStatus,
        ScanVerdict,
    )

    return ClamAVScanner, ClamAVScanError, ScanStatus, ScanVerdict


@asynccontextmanager
async def local_clamd(
    handler: Callable[[asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]],
) -> AsyncIterator[int]:
    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    socket = server.sockets[0]
    port = int(socket.getsockname()[1])
    try:
        yield port
    finally:
        server.close()
        await server.wait_closed()


def scanner_at(port: int, **changes: object) -> object:
    scanner_type, _error_type, _status, _verdict = clamav_contract()
    options: dict[str, object] = {
        "host": "127.0.0.1",
        "port": port,
        "connect_timeout_seconds": 0.25,
        "io_timeout_seconds": 0.25,
        "total_timeout_seconds": 0.75,
        "max_chunk_bytes": 3,
        "max_stream_bytes": 64,
        "max_response_bytes": 64,
    }
    options.update(changes)
    return scanner_type(**options)


async def chunks(*values: bytes) -> AsyncIterator[bytes]:
    for value in values:
        yield value


async def read_instream(
    reader: asyncio.StreamReader,
) -> tuple[bytes, list[bytes]]:
    command = await reader.readuntil(b"\0")
    frames: list[bytes] = []
    while True:
        length = struct.unpack("!I", await reader.readexactly(4))[0]
        if length == 0:
            return command, frames
        frames.append(await reader.readexactly(length))


@pytest.mark.asyncio
async def test_instream_framing_splits_chunks_and_closes_after_clean() -> None:
    """Wrong command, byte order, framing, or close behavior breaks real clamd scans."""
    seen: dict[str, object] = {}
    closed = asyncio.Event()

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            command, frames = await read_instream(reader)
            seen.update(command=command, frames=frames)
            writer.write(b"stream: OK\0")
            await writer.drain()
            await reader.read()
            closed.set()
        finally:
            writer.close()
            await writer.wait_closed()

    async with local_clamd(handler) as port:
        verdict = await scanner_at(port).scan(chunks(b"abcdefg"))
        await asyncio.wait_for(closed.wait(), timeout=1)

    _scanner_type, _error_type, status, _verdict_type = clamav_contract()
    assert verdict.status == status.CLEAN and verdict.signature is None
    assert seen == {
        "command": b"zINSTREAM\0",
        "frames": [b"abc", b"def", b"g"],
    }


@pytest.mark.asyncio
async def test_exact_infected_response_preserves_signature() -> None:
    """Treating a FOUND response as clean would release infected content."""
    closed = asyncio.Event()

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await read_instream(reader)
            writer.write(b"stream: Eicar-Test-Signature FOUND\0")
            await writer.drain()
            await reader.read()
            closed.set()
        finally:
            writer.close()
            await writer.wait_closed()

    async with local_clamd(handler) as port:
        verdict = await scanner_at(port).scan(chunks(b"eicar"))
        await asyncio.wait_for(closed.wait(), timeout=1)

    _scanner_type, _error_type, status, _verdict_type = clamav_contract()
    assert verdict.status == status.INFECTED
    assert verdict.signature == "Eicar-Test-Signature"


@pytest.mark.asyncio
async def test_total_stream_bound_stops_before_oversized_frame_and_closes() -> None:
    """Ignoring StreamMaxLength coordination could overrun clamd's configured limit."""
    received = bytearray()
    closed = asyncio.Event()

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            received.extend(await reader.read())
            closed.set()
        finally:
            writer.close()
            await writer.wait_closed()

    _scanner_type, error_type, _status, _verdict_type = clamav_contract()
    async with local_clamd(handler) as port:
        with pytest.raises(error_type, match="clamav scan failed") as raised:
            await scanner_at(port, max_stream_bytes=4).scan(chunks(b"abc", b"de"))
        await asyncio.wait_for(closed.wait(), timeout=1)

    assert b"de" not in received
    assert "abc" not in str(raised.value)


@pytest.mark.asyncio
async def test_oversized_response_fails_closed_and_closes() -> None:
    """An unbounded clamd response could consume worker memory or inject stored text."""
    closed = asyncio.Event()

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await read_instream(reader)
            writer.write(b"x" * 17 + b"\0")
            await writer.drain()
            await reader.read()
            closed.set()
        finally:
            writer.close()
            await writer.wait_closed()

    _scanner_type, error_type, _status, _verdict_type = clamav_contract()
    async with local_clamd(handler) as port:
        with pytest.raises(error_type, match="clamav scan failed"):
            await scanner_at(port, max_response_bytes=16).scan(chunks(b"safe"))
        await asyncio.wait_for(closed.wait(), timeout=1)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        b"stream: OKAY\0",
        b"stream: FOUND\0",
        b"other: Eicar-Test-Signature FOUND\0",
        b"stream: Eicar-Test-Signature FOUND trailing\0",
        b"stream: OK",
    ],
)
async def test_malformed_or_unterminated_response_fails_closed(response: bytes) -> None:
    """Permissive response parsing could turn protocol corruption into a clean verdict."""
    closed = asyncio.Event()

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await read_instream(reader)
            writer.write(response)
            await writer.drain()
            if not response.endswith(b"\0"):
                writer.write_eof()
            await reader.read()
            closed.set()
        finally:
            writer.close()
            await writer.wait_closed()

    _scanner_type, error_type, _status, _verdict_type = clamav_contract()
    async with local_clamd(handler) as port:
        with pytest.raises(error_type, match="clamav scan failed"):
            await scanner_at(port).scan(chunks(b"safe"))
        await asyncio.wait_for(closed.wait(), timeout=1)


@pytest.mark.asyncio
async def test_response_timeout_is_bounded_and_closes() -> None:
    """Depending only on a worker hard limit would leave the socket hung too long."""
    closed = asyncio.Event()

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await read_instream(reader)
            await reader.read()
            closed.set()
        finally:
            writer.close()
            await writer.wait_closed()

    _scanner_type, error_type, _status, _verdict_type = clamav_contract()
    async with local_clamd(handler) as port:
        with pytest.raises(error_type, match="clamav scan failed"):
            await scanner_at(port, io_timeout_seconds=0.05).scan(chunks(b"safe"))
        await asyncio.wait_for(closed.wait(), timeout=1)


@pytest.mark.asyncio
async def test_producer_failure_is_sanitized_and_closes() -> None:
    """A storage exception must close clamd and must not surface provider text."""
    closed = asyncio.Event()

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await reader.read()
            closed.set()
        finally:
            writer.close()
            await writer.wait_closed()

    async def broken_chunks() -> AsyncIterator[bytes]:
        yield b"safe"
        raise RuntimeError("producer provider secret")

    _scanner_type, error_type, _status, _verdict_type = clamav_contract()
    async with local_clamd(handler) as port:
        with pytest.raises(error_type, match="clamav scan failed") as raised:
            await scanner_at(port).scan(broken_chunks())
        await asyncio.wait_for(closed.wait(), timeout=1)

    assert "secret" not in str(raised.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_chunk", [b"", "not-bytes"])
async def test_invalid_producer_chunk_fails_closed_and_closes(invalid_chunk: object) -> None:
    """Silently accepting empty or non-byte chunks would weaken framing invariants."""
    closed = asyncio.Event()

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await reader.read()
            closed.set()
        finally:
            writer.close()
            await writer.wait_closed()

    async def invalid_chunks() -> AsyncIterator[Any]:
        yield invalid_chunk

    _scanner_type, error_type, _status, _verdict_type = clamav_contract()
    async with local_clamd(handler) as port:
        with pytest.raises(error_type, match="clamav scan failed"):
            await scanner_at(port).scan(invalid_chunks())
        await asyncio.wait_for(closed.wait(), timeout=1)


@pytest.mark.asyncio
async def test_cancellation_propagates_and_closes() -> None:
    """Swallowing cancellation would leak a socket and defeat worker shutdown."""
    producer_entered = asyncio.Event()
    closed = asyncio.Event()

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await reader.read()
            closed.set()
        finally:
            writer.close()
            await writer.wait_closed()

    async def blocked_chunks() -> AsyncIterator[bytes]:
        producer_entered.set()
        await asyncio.Event().wait()
        yield b"unreachable"

    async with local_clamd(handler) as port:
        task = asyncio.create_task(scanner_at(port).scan(blocked_chunks()))
        await asyncio.wait_for(producer_entered.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(closed.wait(), timeout=1)


class SyncCloseFailureWriter:
    def write(self, _payload: bytes) -> None:
        return None

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        raise RuntimeError("provider close secret")

    async def wait_closed(self) -> None:
        raise AssertionError("wait_closed must not run after close failed")


class FixedResponseReader:
    def __init__(self, response: bytes) -> None:
        self.response = response

    async def readuntil(self, _separator: bytes) -> bytes:
        return self.response


@pytest.mark.asyncio
async def test_synchronous_close_failure_does_not_break_clean_verdict(monkeypatch) -> None:
    """A provider close error after an accepted response must not replace CLEAN."""

    async def open_connection(*_args: object, **_kwargs: object) -> tuple[object, object]:
        return FixedResponseReader(b"stream: OK\0"), SyncCloseFailureWriter()

    monkeypatch.setattr(asyncio, "open_connection", open_connection)

    verdict = await scanner_at(3310).scan(chunks(b"safe"))

    _scanner_type, _error_type, status, _verdict_type = clamav_contract()
    assert verdict.status == status.CLEAN


@pytest.mark.asyncio
async def test_synchronous_close_failure_does_not_mask_scan_error(monkeypatch) -> None:
    """A provider close error must not replace the adapter's fixed protocol error."""

    async def open_connection(*_args: object, **_kwargs: object) -> tuple[object, object]:
        return FixedResponseReader(b"malformed\0"), SyncCloseFailureWriter()

    monkeypatch.setattr(asyncio, "open_connection", open_connection)
    _scanner_type, error_type, _status, _verdict_type = clamav_contract()

    with pytest.raises(error_type, match="clamav scan failed") as raised:
        await scanner_at(3310).scan(chunks(b"safe"))

    assert "secret" not in str(raised.value)
