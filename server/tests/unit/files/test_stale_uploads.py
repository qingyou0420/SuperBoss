"""Stale upload recovery, cleanup durability, and hourly task contracts."""

import asyncio
import importlib
import threading
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import pytest
from sqlalchemy import event, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from superboss.modules.audit.models import AuditLog
from superboss.modules.files.models import (
    File,
    FileState,
    FileStorageCleanup,
    FileUploadLifecycle,
    Upload,
)
from superboss.modules.projects.models import Project
from tests.files.storage import InMemoryObjectStorage


def stale_contract() -> tuple[type[Any], Any, Any]:
    from superboss.modules.files.service import StaleUploadService

    schedules = importlib.import_module("superboss.workers.schedules")
    celery_module = importlib.import_module("superboss.workers.celery_app")
    return StaleUploadService, schedules, celery_module.celery_app


def expiry_event_key(file_id: UUID) -> UUID:
    return uuid5(NAMESPACE_URL, f"superboss:file-upload-expired:{file_id}")


async def seed_upload(
    session: AsyncSession,
    *,
    now: datetime,
    age: timedelta = timedelta(hours=25),
    state: FileState = FileState.UPLOADING,
    multipart_id: str | None = "multipart-known",
    provision_state: str | None = None,
    completion_state: str = "NONE",
) -> tuple[UUID, UUID, str]:
    file_id = uuid4()
    upload_id = uuid4()
    project = Project(name=f"Stale {file_id}")
    object_key = f"projects/{project.id}/docs/{file_id}/report.pdf"
    created_at = now - age
    file = File(
        id=file_id,
        project_id=project.id,
        filename="report.pdf",
        category="docs",
        file_date=created_at.date(),
        object_key=object_key,
        size_bytes=1,
        sha256="0" * 64,
        state=state,
        uploader_id=project.id,
        uploader_kind="system",
        content_type="application/pdf",
        created_at=created_at,
        updated_at=created_at,
    )
    upload = Upload(
        id=upload_id,
        file_id=file_id,
        project_id=project.id,
        uploader_id=project.id,
        uploader_kind="system",
        metadata_fingerprint="0" * 64,
        idempotency_key=f"stale-{file_id}",
        multipart_id=multipart_id,
        created_at=created_at,
    )
    lifecycle = FileUploadLifecycle(
        upload_id=upload_id,
        file_id=file_id,
        project_id=project.id,
        object_key=object_key,
        multipart_id=multipart_id,
        content_type="application/pdf",
        declared_size_bytes=1,
        provision_state=provision_state or ("READY" if multipart_id else "PROVISIONING"),
        completion_state=completion_state,
        created_at=created_at,
        updated_at=created_at,
    )
    session.add_all([project, file, upload, lifecycle])
    await session.commit()
    return file_id, upload_id, object_key


async def recovery_rows(
    session: AsyncSession, file_id: UUID, upload_id: UUID
) -> tuple[File, FileUploadLifecycle, list[FileStorageCleanup], list[AuditLog]]:
    session.expire_all()
    file = await session.get(File, file_id)
    lifecycle = await session.get(FileUploadLifecycle, upload_id)
    cleanups = list(
        await session.scalars(
            select(FileStorageCleanup).where(FileStorageCleanup.lifecycle_id == upload_id)
        )
    )
    audits = list(
        await session.scalars(select(AuditLog).where(AuditLog.object_id == file_id))
    )
    assert file is not None and lifecycle is not None
    return file, lifecycle, cleanups, audits


@pytest.mark.asyncio
async def test_known_stale_upload_atomically_fails_audits_and_records_abort(
    db_session: AsyncSession,
) -> None:
    """Direct provider abort before durable state would lose recovery on failure."""
    _service_type, schedules, _app = stale_contract()
    now = datetime(2026, 8, 9, 12, tzinfo=UTC)
    file_id, upload_id, _object_key = await seed_upload(db_session, now=now)
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    assert (
        await schedules.execute_stale_upload_recovery(
            session_factory=factory,
            now=now,
        )
        == 1
    )

    file, lifecycle, cleanups, audits = await recovery_rows(db_session, file_id, upload_id)
    assert file.state == FileState.FAILED and file.scan_result == "UPLOAD_EXPIRED"
    assert lifecycle.provision_state == "TERMINAL"
    assert len(cleanups) == 1 and cleanups[0].operation == "ABORT_MULTIPART"
    assert cleanups[0].state == "PENDING" and cleanups[0].multipart_id == "multipart-known"
    assert len(audits) == 1
    assert audits[0].event_key == expiry_event_key(file_id)
    assert audits[0].actor_kind == "system" and audits[0].action == "file.upload.expire"
    assert audits[0].metadata_json == {
        "actor_role": None,
        "reason": "UPLOAD_EXPIRED",
        "state": "FAILED",
    }


@pytest.mark.asyncio
async def test_unknown_provisioning_upload_records_discovery_cleanup(
    db_session: AsyncSession,
) -> None:
    """Treating an unknown multipart ID as no work could orphan provider state."""
    service_type, _schedules, _app = stale_contract()
    now = datetime(2026, 8, 9, 12, tzinfo=UTC)
    file_id, upload_id, _object_key = await seed_upload(
        db_session,
        now=now,
        multipart_id=None,
        provision_state="PROVISIONING",
    )
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    assert await service_type(factory).recover_stale_uploads(now=now) == 1

    file, lifecycle, cleanups, audits = await recovery_rows(db_session, file_id, upload_id)
    assert file.state == FileState.FAILED and file.scan_result == "UPLOAD_EXPIRED"
    assert lifecycle.provision_state == "TERMINAL"
    assert len(cleanups) == 1 and cleanups[0].operation == "DISCOVER_MULTIPART"
    assert cleanups[0].multipart_id is None and len(audits) == 1


@pytest.mark.asyncio
async def test_concurrent_and_repeated_recovery_has_one_transition_and_audit(
    db_session: AsyncSession,
) -> None:
    """Missing final row locks or dedupe would duplicate expiry evidence and cleanup."""
    service_type, _schedules, _app = stale_contract()
    now = datetime(2026, 8, 9, 12, tzinfo=UTC)
    file_id, upload_id, _object_key = await seed_upload(db_session, now=now)
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    results = await asyncio.gather(
        service_type(factory).recover_stale_uploads(now=now),
        service_type(factory).recover_stale_uploads(now=now),
    )
    replay = await service_type(factory).recover_stale_uploads(now=now)

    _file, _lifecycle, cleanups, audits = await recovery_rows(db_session, file_id, upload_id)
    assert sum(results) == 1 and replay == 0
    assert len(cleanups) == 1 and len(audits) == 1
    assert audits[0].event_key == expiry_event_key(file_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state", "age", "completion_state"),
    [
        (FileState.UPLOADING, timedelta(hours=23, minutes=59), "NONE"),
        (FileState.QUARANTINED, timedelta(hours=25), "QUARANTINED"),
        (FileState.CLEAN, timedelta(hours=25), "QUARANTINED"),
    ],
)
async def test_recovery_skips_fresh_or_completed_files(
    db_session: AsyncSession,
    state: FileState,
    age: timedelta,
    completion_state: str,
) -> None:
    """A broad age query could fail an active or already completed file."""
    service_type, _schedules, _app = stale_contract()
    now = datetime(2026, 8, 9, 12, tzinfo=UTC)
    file_id, upload_id, _object_key = await seed_upload(
        db_session,
        now=now,
        age=age,
        state=state,
        completion_state=completion_state,
    )
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    assert await service_type(factory).recover_stale_uploads(now=now) == 0

    file, lifecycle, cleanups, audits = await recovery_rows(db_session, file_id, upload_id)
    assert file.state == state and lifecycle.provision_state == "READY"
    assert cleanups == [] and audits == []


@pytest.mark.asyncio
async def test_audit_conflict_rolls_back_file_lifecycle_and_cleanup(
    db_session: AsyncSession,
) -> None:
    """Writing audit in a separate transaction could expose a half-terminalized upload."""
    service_type, _schedules, _app = stale_contract()
    now = datetime(2026, 8, 9, 12, tzinfo=UTC)
    file_id, upload_id, _object_key = await seed_upload(db_session, now=now)
    db_session.add(
        AuditLog(
            actor_kind="user",
            actor_id=None,
            action="conflicting.event",
            object_type="file",
            object_id=file_id,
            project_id=None,
            outcome="SUCCESS",
            metadata_json={},
            request_id=uuid4(),
            event_key=expiry_event_key(file_id),
        )
    )
    await db_session.commit()
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    with pytest.raises(RuntimeError, match="stale upload audit conflict"):
        await service_type(factory).recover_stale_uploads(now=now)

    file, lifecycle, cleanups, audits = await recovery_rows(db_session, file_id, upload_id)
    assert file.state == FileState.UPLOADING and file.scan_result is None
    assert lifecycle.provision_state == "READY"
    assert cleanups == [] and len(audits) == 1 and audits[0].action == "conflicting.event"


@pytest.mark.asyncio
async def test_cleanup_provider_failure_remains_pending_then_retries(
    db_session: AsyncSession,
) -> None:
    """Provider failure after expiry must retain executable cleanup work."""
    service_type, _schedules, _app = stale_contract()
    now = datetime(2026, 8, 9, 12, tzinfo=UTC)
    file_id, upload_id, object_key = await seed_upload(db_session, now=now)
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    assert await service_type(factory).recover_stale_uploads(now=now) == 1
    storage = InMemoryObjectStorage(abort_error=RuntimeError("provider secret"))
    storage.active["multipart-known"] = object_key

    from superboss.modules.files.service import FileLifecycleService

    assert await FileLifecycleService(factory, storage).reconcile_cleanup(limit=1) == 0
    _file, _lifecycle, cleanups, _audits = await recovery_rows(
        db_session, file_id, upload_id
    )
    assert len(cleanups) == 1
    assert cleanups[0].state == "PENDING" and cleanups[0].last_error_code == "ABORT_FAILED"
    assert "secret" not in cleanups[0].last_error_code

    storage.abort_error = None
    cleanups[0].next_attempt_at = datetime.now(UTC)
    await db_session.commit()
    assert await FileLifecycleService(factory, storage).reconcile_cleanup(limit=1) == 1
    _file, _lifecycle, cleanups, _audits = await recovery_rows(
        db_session, file_id, upload_id
    )
    assert cleanups[0].state == "DONE" and cleanups[0].attempt_count == 2


@pytest.mark.asyncio
async def test_recovery_holds_file_lock_before_waiting_for_lifecycle(
    db_session: AsyncSession,
) -> None:
    """Reversing File-to-lifecycle order would reintroduce Task 7 delete deadlocks."""
    service_type, _schedules, _app = stale_contract()
    now = datetime(2026, 8, 9, 12, tzinfo=UTC)
    file_id, upload_id, _object_key = await seed_upload(db_session, now=now)
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    reached_lifecycle_lock = threading.Event()

    async with factory() as blocker:
        await blocker.execute(
            select(FileUploadLifecycle)
            .where(FileUploadLifecycle.upload_id == upload_id)
            .with_for_update()
        )

        def observe_lifecycle_select(
            _connection: object,
            _cursor: object,
            statement: str,
            _parameters: object,
            _context: object,
            _executemany: bool,
        ) -> None:
            if "file_upload_lifecycle" in statement and "FOR UPDATE" in statement:
                reached_lifecycle_lock.set()

        assert db_session.bind is not None
        event.listen(db_session.bind.sync_engine, "before_cursor_execute", observe_lifecycle_select)
        task = asyncio.create_task(service_type(factory).recover_stale_uploads(now=now))
        try:
            assert await asyncio.to_thread(reached_lifecycle_lock.wait, 1)
            async with factory() as contender:
                await contender.execute(text("SET LOCAL lock_timeout = '100ms'"))
                with pytest.raises(DBAPIError):
                    await contender.execute(
                        update(File).where(File.id == file_id).values(filename="blocked.pdf")
                    )
                await contender.rollback()
        finally:
            event.remove(
                db_session.bind.sync_engine,
                "before_cursor_execute",
                observe_lifecycle_select,
            )
            await blocker.rollback()
        assert await asyncio.wait_for(task, timeout=3) == 1


def test_hourly_stale_task_has_stable_name_options_and_schedule() -> None:
    """Missing beat or worker-loss options would silently stop expiry maintenance."""
    _service_type, schedules, app = stale_contract()
    task = schedules.recover_stale_uploads_task
    entry = app.conf.beat_schedule["recover-stale-uploads-hourly"]

    assert task.name == "superboss.files.recover_stale_uploads"
    assert task.acks_late is True and task.reject_on_worker_lost is True
    assert task.max_retries == 3 and task.retry_backoff is True
    assert entry == {
        "task": task.name,
        "schedule": 3600.0,
        "options": {"queue": "file-maintenance"},
    }
    assert app.conf.task_routes[task.name]["queue"] == "file-maintenance"


def test_stale_task_drives_async_execution(monkeypatch) -> None:
    """A registered beat task that never awaits recovery would be a silent no-op."""
    _service_type, schedules, _app = stale_contract()
    seen: list[bool] = []

    async def execute() -> int:
        seen.append(True)
        return 7

    monkeypatch.setattr(schedules, "_run_stale_upload_recovery", execute)

    assert schedules.recover_stale_uploads_task.run() == 7
    assert seen == [True]
