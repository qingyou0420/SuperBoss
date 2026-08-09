"""Import reconciliation ordering at the existing file-scan task boundary."""

import asyncio
import hashlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session

from superboss.infrastructure.clamav import ScanStatus, ScanVerdict
from superboss.modules.files.models import File, FileState
from superboss.modules.imports.service import ImportService
from superboss.modules.projects.models import Project


@dataclass
class ScanStorage:
    content: bytes
    stream_calls: int = 0

    def stream(self, _object_key: str) -> AsyncIterator[bytes]:
        self.stream_calls += 1

        async def chunks() -> AsyncIterator[bytes]:
            yield self.content

        return chunks()


class RecordingScanner:
    def __init__(self, result: ScanVerdict | Exception) -> None:
        self.result = result
        self.calls = 0

    async def scan(self, chunks: AsyncIterator[bytes]) -> ScanVerdict:
        self.calls += 1
        async for _chunk in chunks:
            pass
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class CancellingScanner:
    def __init__(self) -> None:
        self.calls = 0
        self.entered = asyncio.Event()

    async def scan(self, chunks: AsyncIterator[bytes]) -> ScanVerdict:
        self.calls += 1
        async for _chunk in chunks:
            break
        self.entered.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


async def scan_file_fixture(
    db_session: AsyncSession,
    *,
    content: bytes,
    state: FileState = FileState.QUARANTINED,
) -> File:
    project = Project(name=f"Import scan integration {uuid4()}")
    db_session.add(project)
    await db_session.flush()
    file = File(
        project_id=project.id,
        filename="k3.json",
        category="kimi-imports",
        file_date=date(2026, 8, 9),
        object_key=f"projects/{project.id}/kimi-imports/k3.json",
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        state=state,
        uploader_id=project.id,
        uploader_kind="system",
        content_type="application/json",
        scan_result="CLEAN" if state == FileState.CLEAN else None,
    )
    db_session.add(file)
    await db_session.commit()
    return file


async def saved_file(
    session_factory: async_sessionmaker[AsyncSession], file_id: UUID
) -> File:
    async with session_factory() as session:
        file = await session.get(File, file_id)
        assert file is not None
        return file


def spy_on_reconcile(
    monkeypatch: pytest.MonkeyPatch,
    session_factory: async_sessionmaker[AsyncSession],
    observed: list[tuple[UUID, FileState, str | None]],
) -> None:
    async def reconcile_file(_service: ImportService, file_id: UUID) -> None:
        async with session_factory() as session:
            file = await session.get(File, file_id)
            assert file is not None
            observed.append((file_id, file.state, file.scan_result))

    monkeypatch.setattr(ImportService, "reconcile_file", reconcile_file, raising=False)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scanner_result", "expected_state", "expected_scan_result"),
    [
        (ScanVerdict(ScanStatus.CLEAN), FileState.CLEAN, "CLEAN"),
        (RuntimeError("scanner provider secret"), FileState.FAILED, "SCAN_FAILED"),
    ],
)
async def test_scan_execution_reconciles_only_after_terminal_file_commit(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    scanner_result: ScanVerdict | Exception,
    expected_state: FileState,
    expected_scan_result: str,
) -> None:
    """The callback must observe the committed clean/failed file, never SCANNING."""
    from superboss.modules.files import tasks

    content = b"committed import scan"
    file = await scan_file_fixture(db_session, content=content)
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    storage = ScanStorage(content)
    scanner = RecordingScanner(scanner_result)
    observed: list[tuple[UUID, FileState, str | None]] = []
    spy_on_reconcile(monkeypatch, session_factory, observed)

    await tasks.execute_file_scan(
        str(file.id),
        session_factory=session_factory,
        storage=storage,
        scanner=scanner,
    )

    assert observed == [(file.id, expected_state, expected_scan_result)]
    assert storage.stream_calls == scanner.calls == 1


@pytest.mark.asyncio
async def test_cancelled_scan_rolls_back_without_import_reconcile(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancellation keeps QUARANTINED retryability and must never publish a false callback."""
    from superboss.modules.files import tasks

    content = b"cancelled import scan"
    file = await scan_file_fixture(db_session, content=content)
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    storage = ScanStorage(content)
    scanner = CancellingScanner()
    observed: list[tuple[UUID, FileState, str | None]] = []
    spy_on_reconcile(monkeypatch, session_factory, observed)
    execution = asyncio.create_task(
        tasks.execute_file_scan(
            str(file.id),
            session_factory=session_factory,
            storage=storage,
            scanner=scanner,
        )
    )
    await asyncio.wait_for(scanner.entered.wait(), timeout=3)

    execution.cancel()
    with pytest.raises(asyncio.CancelledError):
        await execution

    persisted = await saved_file(session_factory, file.id)
    assert persisted.state == FileState.QUARANTINED and persisted.scan_result is None
    assert observed == [] and storage.stream_calls == scanner.calls == 1


@pytest.mark.asyncio
async def test_scan_commit_failure_rolls_back_without_import_reconcile(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A database commit failure must propagate without advancing any import observer."""
    from superboss.modules.files import tasks

    content = b"rolled back import scan"
    file = await scan_file_fixture(db_session, content=content)
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    storage = ScanStorage(content)
    scanner = RecordingScanner(ScanVerdict(ScanStatus.CLEAN))
    observed: list[tuple[UUID, FileState, str | None]] = []
    spy_on_reconcile(monkeypatch, session_factory, observed)

    def fail_scan_commit(session: Session) -> None:
        if any(
            isinstance(candidate, File)
            and candidate.id == file.id
            and candidate.state == FileState.CLEAN
            for candidate in session.dirty
        ):
            raise RuntimeError("database commit unavailable")

    event.listen(Session, "before_commit", fail_scan_commit)
    try:
        with pytest.raises(RuntimeError, match="database commit unavailable"):
            await tasks.execute_file_scan(
                str(file.id),
                session_factory=session_factory,
                storage=storage,
                scanner=scanner,
            )
    finally:
        event.remove(Session, "before_commit", fail_scan_commit)

    persisted = await saved_file(session_factory, file.id)
    assert persisted.state == FileState.QUARANTINED and persisted.scan_result is None
    assert observed == [] and storage.stream_calls == scanner.calls == 1


@pytest.mark.asyncio
async def test_terminal_scan_task_replay_reconciles_without_storage_or_scanner_io(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task retry may re-run DB reconciliation while terminal File scanning remains a no-op."""
    from superboss.modules.files import tasks

    content = b"terminal import scan"
    file = await scan_file_fixture(db_session, content=content, state=FileState.CLEAN)
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    storage = ScanStorage(content)
    scanner = RecordingScanner(ScanVerdict(ScanStatus.CLEAN))
    observed: list[tuple[UUID, FileState, str | None]] = []
    spy_on_reconcile(monkeypatch, session_factory, observed)

    for _ in range(2):
        await tasks.execute_file_scan(
            str(file.id),
            session_factory=session_factory,
            storage=storage,
            scanner=scanner,
        )

    assert observed == [
        (file.id, FileState.CLEAN, "CLEAN"),
        (file.id, FileState.CLEAN, "CLEAN"),
    ]
    assert storage.stream_calls == scanner.calls == 0
