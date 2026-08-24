"""Stale upload expiry, lost-scan redispatch, and hourly task contracts."""

import asyncio
import importlib
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from superboss.modules.files.models import File, FileState, Upload
from superboss.modules.projects.models import Project
from tests.files.storage import InMemoryObjectStorage


def stale_contract() -> tuple[type[Any], Any, Any]:
    from superboss.modules.files.service import StaleUploadService

    schedules = importlib.import_module("superboss.workers.schedules")
    celery_module = importlib.import_module("superboss.workers.celery_app")
    return StaleUploadService, schedules, celery_module.celery_app


async def seed_upload(
    session: AsyncSession,
    *,
    now: datetime,
    age: timedelta = timedelta(hours=25),
    state: FileState = FileState.UPLOADING,
    multipart_id: str | None = "multipart-known",
) -> tuple[UUID, UUID, str]:
    file_id = uuid4()
    upload_id = uuid4()
    project_id = uuid4()
    project = Project(id=project_id, name=f"Stale {file_id}")
    session.add(project)
    await session.flush()
    object_key = f"projects/{project_id}/docs/{file_id}/report.pdf"
    created_at = now - age
    file = File(
        id=file_id,
        project_id=project_id,
        filename="report.pdf",
        category="docs",
        file_date=created_at.date(),
        object_key=object_key,
        size_bytes=1,
        sha256="0" * 64,
        state=state,
        uploader_id=project_id,
        uploader_kind="system",
        content_type="application/pdf",
        created_at=created_at,
        updated_at=created_at,
    )
    upload = Upload(
        id=upload_id,
        file_id=file_id,
        project_id=project_id,
        uploader_id=project_id,
        uploader_kind="system",
        metadata_fingerprint="0" * 64,
        idempotency_key=f"stale-{file_id}",
        multipart_id=multipart_id,
        created_at=created_at,
    )
    session.add_all([file, upload])
    await session.commit()
    return file_id, upload_id, object_key


@pytest.mark.asyncio
async def test_known_stale_upload_fails_and_aborts_multipart(
    db_session: AsyncSession,
) -> None:
    service_type, _schedules, _app = stale_contract()
    now = datetime(2026, 8, 9, 12, tzinfo=UTC)
    file_id, _upload_id, object_key = await seed_upload(db_session, now=now)
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    storage = InMemoryObjectStorage()
    storage.active["multipart-known"] = object_key

    assert await service_type(factory, storage).recover_stale_uploads(now=now) == 1

    db_session.expire_all()
    file = await db_session.get(File, file_id)
    assert file is not None
    assert file.state == FileState.FAILED and file.scan_result == "UPLOAD_EXPIRED"
    assert "multipart-known" in storage.aborted
    assert object_key in storage.deleted


@pytest.mark.asyncio
async def test_unknown_multipart_still_expires_the_file(
    db_session: AsyncSession,
) -> None:
    service_type, _schedules, _app = stale_contract()
    now = datetime(2026, 8, 9, 12, tzinfo=UTC)
    file_id, _upload_id, object_key = await seed_upload(
        db_session,
        now=now,
        multipart_id=None,
    )
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    storage = InMemoryObjectStorage()

    assert await service_type(factory, storage).recover_stale_uploads(now=now) == 1

    db_session.expire_all()
    file = await db_session.get(File, file_id)
    assert file is not None
    assert file.state == FileState.FAILED and file.scan_result == "UPLOAD_EXPIRED"
    assert storage.aborted == set()
    assert object_key in storage.deleted


@pytest.mark.asyncio
async def test_concurrent_and_repeated_recovery_has_one_transition(
    db_session: AsyncSession,
) -> None:
    service_type, _schedules, _app = stale_contract()
    now = datetime(2026, 8, 9, 12, tzinfo=UTC)
    file_id, _upload_id, _object_key = await seed_upload(db_session, now=now)
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    results = await asyncio.gather(
        service_type(factory).recover_stale_uploads(now=now),
        service_type(factory).recover_stale_uploads(now=now),
    )
    replay = await service_type(factory).recover_stale_uploads(now=now)

    db_session.expire_all()
    file = await db_session.get(File, file_id)
    assert file is not None and file.state == FileState.FAILED
    assert sum(results) == 1 and replay == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state", "age"),
    [
        (FileState.UPLOADING, timedelta(hours=23, minutes=59)),
        (FileState.CLEAN, timedelta(hours=25)),
    ],
)
async def test_recovery_skips_fresh_or_completed_files(
    db_session: AsyncSession,
    state: FileState,
    age: timedelta,
) -> None:
    service_type, _schedules, _app = stale_contract()
    now = datetime(2026, 8, 9, 12, tzinfo=UTC)
    file_id, _upload_id, _object_key = await seed_upload(
        db_session,
        now=now,
        age=age,
        state=state,
    )
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    assert await service_type(factory).recover_stale_uploads(now=now) == 0

    db_session.expire_all()
    file = await db_session.get(File, file_id)
    assert file is not None and file.state == state


@pytest.mark.asyncio
async def test_quarantined_file_is_redispatched_after_retry_age(
    db_session: AsyncSession,
) -> None:
    service_type, _schedules, _app = stale_contract()
    now = datetime(2026, 8, 9, 12, tzinfo=UTC)
    file_id, _upload_id, _object_key = await seed_upload(
        db_session,
        now=now,
        age=timedelta(minutes=20),
        state=FileState.QUARANTINED,
    )
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    dispatched: list[UUID] = []

    recovered = await service_type(
        factory,
        enqueue_scan=lambda file_id, _key: dispatched.append(file_id),
    ).recover_stale_uploads(now=now)

    assert recovered == 1
    assert dispatched == [file_id]
    db_session.expire_all()
    file = await db_session.get(File, file_id)
    assert file is not None and file.state == FileState.QUARANTINED
