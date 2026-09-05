"""Quarantine scan state-machine behavior against real PostgreSQL locking."""

import asyncio
import hashlib
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from superboss.modules.files.models import File, FileState
from tests.files.factory import add_folder
from tests.identity import local_user


@dataclass
class ChunkStorage:
    chunks: list[bytes]
    stream_calls: int = 0

    def stream(self, object_key: str) -> AsyncIterator[bytes]:
        self.stream_calls += 1

        async def produce() -> AsyncIterator[bytes]:
            for chunk in self.chunks:
                yield chunk

        return produce()


@dataclass
class RecordingScanner:
    verdict: object
    calls: int = 0
    received: list[bytes] = field(default_factory=list)

    async def scan(self, chunks: AsyncIterator[bytes]) -> object:
        self.calls += 1
        self.received = [chunk async for chunk in chunks]
        return self.verdict


class TimeoutScanner:
    calls = 0

    async def scan(self, chunks: AsyncIterator[bytes]) -> object:
        self.calls += 1
        async for _chunk in chunks:
            pass
        raise TimeoutError("clamd provider secret")


class BlockingCleanScanner:
    def __init__(self, verdict: object) -> None:
        self.verdict = verdict
        self.calls = 0
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def scan(self, chunks: AsyncIterator[bytes]) -> object:
        self.calls += 1
        async for _chunk in chunks:
            pass
        self.entered.set()
        await asyncio.wait_for(self.release.wait(), timeout=3)
        return self.verdict


class CancellingScanner:
    def __init__(self) -> None:
        self.entered = asyncio.Event()

    async def scan(self, chunks: AsyncIterator[bytes]) -> object:
        async for _chunk in chunks:
            break
        self.entered.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


def scan_contract() -> tuple[type[Any], type[Any], Any]:
    """Load the wished-for production API inside each test so RED is a test failure."""
    from superboss.infrastructure.clamav import ScanStatus, ScanVerdict
    from superboss.modules.files.service import FileScanService

    return FileScanService, ScanVerdict, ScanStatus


async def quarantined_file(
    session: AsyncSession,
    *,
    state: FileState = FileState.QUARANTINED,
    content: bytes = b"safe document",
) -> File:
    owner = local_user(f"scan{id(content) % 10_000_000:07d}", display_name="Scan")
    session.add(owner)
    await session.flush()
    folder = await add_folder(session, owner.id)
    file = File(
        folder_id=folder.id,
        filename="report.pdf",
        object_key=f"folders/{folder.id}/docs/report.pdf",
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        state=state,
        uploader_id=owner.id,
        content_type="application/pdf",
    )
    session.add(file)
    await session.commit()
    return file


async def persisted_file(factory: async_sessionmaker[AsyncSession], file_id: UUID) -> File:
    async with factory() as session:
        file = await session.get(File, file_id)
        assert file is not None
        return file


@pytest.mark.asyncio
async def test_clean_verdict_releases_file(db_session: AsyncSession) -> None:
    """Removing the CLEAN terminal transition would leave verified content unavailable."""
    service_type, verdict_type, status = scan_contract()
    content = b"safe document"
    file = await quarantined_file(db_session, content=content)
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    storage = ChunkStorage([b"safe ", b"document"])
    scanner = RecordingScanner(verdict_type(status.CLEAN))

    await service_type(factory, storage, scanner).scan_file(file.id)

    saved = await persisted_file(factory, file.id)
    assert saved.state == FileState.CLEAN
    assert saved.scan_result == "CLEAN"
    assert scanner.received == [b"safe ", b"document"]


@pytest.mark.asyncio
async def test_infected_verdict_never_releases_file(db_session: AsyncSession) -> None:
    """Mapping an infected verdict to CLEAN would expose quarantined material."""
    service_type, verdict_type, status = scan_contract()
    content = b"unsafe document"
    file = await quarantined_file(db_session, content=content)
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    storage = ChunkStorage([content])
    scanner = RecordingScanner(verdict_type(status.INFECTED, "Eicar-Test-Signature"))

    await service_type(factory, storage, scanner).scan_file(file.id)

    saved = await persisted_file(factory, file.id)
    assert saved.state == FileState.INFECTED
    assert saved.scan_result == "Eicar-Test-Signature"


@pytest.mark.asyncio
async def test_scanner_timeout_fails_closed_without_provider_text(
    db_session: AsyncSession,
) -> None:
    """Propagating or storing a scanner timeout would leak details and permit retries."""
    service_type, _verdict_type, _status = scan_contract()
    content = b"timeout document"
    file = await quarantined_file(db_session, content=content)
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    scanner = TimeoutScanner()

    await service_type(factory, ChunkStorage([content]), scanner).scan_file(file.id)

    saved = await persisted_file(factory, file.id)
    assert saved.state == FileState.FAILED
    assert saved.scan_result == "SCAN_FAILED"
    assert "secret" not in saved.scan_result


@pytest.mark.asyncio
async def test_hash_mismatch_fails_after_scanning_the_stream(db_session: AsyncSession) -> None:
    """Skipping the same-stream digest check would release substituted object bytes."""
    service_type, verdict_type, status = scan_contract()
    file = await quarantined_file(db_session, content=b"declared bytes")
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    scanner = RecordingScanner(verdict_type(status.CLEAN))

    await service_type(factory, ChunkStorage([b"tampered bytes"]), scanner).scan_file(file.id)

    saved = await persisted_file(factory, file.id)
    assert saved.state == FileState.FAILED
    assert saved.scan_result == "HASH_MISMATCH"
    assert scanner.received == [b"tampered bytes"]


@pytest.mark.asyncio
async def test_terminal_replay_performs_no_storage_or_scanner_work(
    db_session: AsyncSession,
) -> None:
    """Replaying a delivered CLEAN task must not read object bytes or rescan."""
    service_type, verdict_type, status = scan_contract()
    content = b"already clean"
    file = await quarantined_file(db_session, state=FileState.CLEAN, content=content)
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    storage = ChunkStorage([content])
    scanner = RecordingScanner(verdict_type(status.CLEAN))

    await service_type(factory, storage, scanner).scan_file(file.id)

    saved = await persisted_file(factory, file.id)
    assert saved.state == FileState.CLEAN
    assert storage.stream_calls == 0
    assert scanner.calls == 0


@pytest.mark.asyncio
async def test_concurrent_scan_deliveries_call_scanner_once(
    db_session: AsyncSession,
) -> None:
    """Dropping the File row lock would let duplicate deliveries scan the object twice."""
    service_type, verdict_type, status = scan_contract()
    content = b"one scan"
    file = await quarantined_file(db_session, content=content)
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    storage = ChunkStorage([content])
    scanner = BlockingCleanScanner(verdict_type(status.CLEAN))
    service = service_type(factory, storage, scanner)

    first = asyncio.create_task(service.scan_file(file.id))
    await asyncio.wait_for(scanner.entered.wait(), timeout=3)
    second = asyncio.create_task(service.scan_file(file.id))
    await asyncio.sleep(0.05)
    assert scanner.calls == 1 and not second.done()
    scanner.release.set()
    await asyncio.wait_for(asyncio.gather(first, second), timeout=3)

    saved = await persisted_file(factory, file.id)
    assert saved.state == FileState.CLEAN
    assert storage.stream_calls == 1
    assert scanner.calls == 1


@pytest.mark.asyncio
async def test_cancelled_scan_rolls_back_to_retryable_quarantine(
    db_session: AsyncSession,
) -> None:
    """Committing SCANNING before external work would strand a cancelled delivery."""
    service_type, _verdict_type, _status = scan_contract()
    content = b"retry after cancellation"
    file = await quarantined_file(db_session, content=content)
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    scanner = CancellingScanner()
    task = asyncio.create_task(
        service_type(factory, ChunkStorage([content]), scanner).scan_file(file.id)
    )
    await asyncio.wait_for(scanner.entered.wait(), timeout=3)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    saved = await persisted_file(factory, file.id)
    assert saved.state == FileState.QUARANTINED
    assert saved.scan_result is None
