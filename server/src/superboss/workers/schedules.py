"""Periodic Celery work for durable file lifecycle maintenance."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from superboss.core.config import get_settings
from superboss.infrastructure.s3 import Boto3ObjectStorage
from superboss.modules.files.service import FileLifecycleService, StaleUploadService
from superboss.modules.files.storage import ObjectStorage
from superboss.modules.files.tasks import enqueue_file_scan
from superboss.workers.celery_app import celery_app


async def execute_lifecycle_reconcile(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    storage: ObjectStorage,
    enqueue_scan: Callable[[UUID, UUID], Awaitable[None] | None],
    limit: int = 100,
) -> int:
    return await FileLifecycleService(session_factory, storage, enqueue_scan).reconcile(limit=limit)


async def _run_lifecycle_reconcile() -> int:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    try:
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        storage = Boto3ObjectStorage(
            settings.s3_bucket,
            settings.s3_endpoint_url,
            settings.s3_access_key_id,
            settings.s3_secret_access_key,
        )
        return await execute_lifecycle_reconcile(
            session_factory=session_factory,
            storage=storage,
            enqueue_scan=enqueue_file_scan,
            limit=100,
        )
    finally:
        await engine.dispose()


@celery_app.task(  # type: ignore[untyped-decorator]
    name="superboss.files.reconcile_lifecycle",
    acks_late=True,
    autoretry_for=(Exception,),
    max_retries=3,
    reject_on_worker_lost=True,
    retry_backoff=True,
    retry_backoff_max=60,
    retry_kwargs={"max_retries": 3},
    soft_time_limit=300,
    time_limit=360,
    queue="file-maintenance",
)
def reconcile_file_lifecycle_task() -> int:
    return asyncio.run(_run_lifecycle_reconcile())


async def execute_stale_upload_recovery(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    now: datetime | None = None,
) -> int:
    return await StaleUploadService(session_factory).recover_stale_uploads(now=now)


async def _run_stale_upload_recovery() -> int:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    try:
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        return await execute_stale_upload_recovery(session_factory=session_factory)
    finally:
        await engine.dispose()


@celery_app.task(  # type: ignore[untyped-decorator]
    name="superboss.files.recover_stale_uploads",
    acks_late=True,
    autoretry_for=(Exception,),
    max_retries=3,
    reject_on_worker_lost=True,
    retry_backoff=True,
    retry_backoff_max=60,
    retry_kwargs={"max_retries": 3},
    soft_time_limit=300,
    time_limit=360,
    queue="file-maintenance",
)
def recover_stale_uploads_task() -> int:
    return asyncio.run(_run_stale_upload_recovery())
