"""Scanner contracts shared by file lifecycle work and the clamd adapter."""

import asyncio
import struct
from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class ScanStatus(StrEnum):
    CLEAN = "CLEAN"
    INFECTED = "INFECTED"


@dataclass(frozen=True)
class ScanVerdict:
    status: ScanStatus
    signature: str | None = None


class Scanner(Protocol):
    async def scan(self, chunks: AsyncIterator[bytes]) -> ScanVerdict: ...


class ClamAVScanError(RuntimeError):
    """Fixed local failure that never includes provider or content details."""

    def __init__(self) -> None:
        super().__init__("clamav scan failed")


@dataclass(frozen=True)
class ClamAVScanner:
    host: str
    port: int = 3310
    connect_timeout_seconds: float = 3.0
    io_timeout_seconds: float = 10.0
    total_timeout_seconds: float = 120.0
    max_chunk_bytes: int = 1024 * 1024
    max_stream_bytes: int = 100 * 1024 * 1024
    max_response_bytes: int = 1024

    def __post_init__(self) -> None:
        if (
            not self.host
            or not 1 <= self.port <= 65535
            or self.connect_timeout_seconds <= 0
            or self.io_timeout_seconds <= 0
            or self.total_timeout_seconds <= 0
            or not 1 <= self.max_chunk_bytes <= 0xFFFFFFFF
            or self.max_stream_bytes < 1
            or self.max_response_bytes < 1
        ):
            raise ValueError("invalid clamav scanner configuration")

    async def scan(self, chunks: AsyncIterator[bytes]) -> ScanVerdict:
        writer: asyncio.StreamWriter | None = None
        try:
            async with asyncio.timeout(self.total_timeout_seconds):
                reader, connected_writer = await asyncio.wait_for(
                    asyncio.open_connection(
                        self.host,
                        self.port,
                        limit=self.max_response_bytes + 1,
                    ),
                    timeout=self.connect_timeout_seconds,
                )
                writer = connected_writer
                await self._write(connected_writer, b"zINSTREAM\0")
                total_bytes = 0
                async for chunk in chunks:
                    if not isinstance(chunk, bytes) or not chunk:
                        raise ClamAVScanError()
                    for offset in range(0, len(chunk), self.max_chunk_bytes):
                        frame = chunk[offset : offset + self.max_chunk_bytes]
                        if total_bytes > self.max_stream_bytes - len(frame):
                            raise ClamAVScanError()
                        await self._write(
                            connected_writer, struct.pack("!I", len(frame)) + frame
                        )
                        total_bytes += len(frame)
                await self._write(connected_writer, struct.pack("!I", 0))
                response = await asyncio.wait_for(
                    reader.readuntil(b"\0"), timeout=self.io_timeout_seconds
                )
                if len(response) - 1 > self.max_response_bytes:
                    raise ClamAVScanError()
                return self._parse_response(response[:-1])
        except asyncio.CancelledError:
            raise
        except ClamAVScanError:
            raise
        except Exception:  # noqa: BLE001 -- collapse all provider failures to a fixed local error
            raise ClamAVScanError() from None
        finally:
            if writer is not None:
                try:
                    writer.close()
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001,S110 -- close cannot leak or mask scan outcome
                    pass
                else:
                    try:
                        await asyncio.wait_for(
                            writer.wait_closed(), timeout=self.io_timeout_seconds
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception:  # noqa: BLE001,S110 -- close cannot leak or mask scan outcome
                        pass

    async def _write(self, writer: asyncio.StreamWriter, payload: bytes) -> None:
        writer.write(payload)
        await asyncio.wait_for(writer.drain(), timeout=self.io_timeout_seconds)

    @staticmethod
    def _parse_response(response: bytes) -> ScanVerdict:
        if response == b"stream: OK":
            return ScanVerdict(ScanStatus.CLEAN)
        prefix = b"stream: "
        suffix = b" FOUND"
        if not response.startswith(prefix) or not response.endswith(suffix):
            raise ClamAVScanError()
        signature_bytes = response[len(prefix) : -len(suffix)]
        if not signature_bytes:
            raise ClamAVScanError()
        try:
            signature = signature_bytes.decode("ascii")
        except UnicodeDecodeError:
            raise ClamAVScanError() from None
        return ScanVerdict(ScanStatus.INFECTED, signature)
