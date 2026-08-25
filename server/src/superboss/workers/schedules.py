"""Periodic Celery work for durable file lifecycle maintenance."""

import asyncio
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from superboss.core.config import get_settings
from superboss.infrastructure.s3 import Boto3ObjectStorage
from superboss.modules.files.service import StaleUploadService
from superboss.modules.files.storage import ObjectStorage
from superboss.modules.files.tasks import enqueue_file_scan
from superboss.workers.celery_app import celery_app


async def execute_stale_upload_recovery(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    storage: ObjectStorage | None = None,
    now: datetime | None = None,
) -> int:
    return await StaleUploadService(
        session_factory,
        storage,
        enqueue_file_scan,
    ).recover_stale_uploads(now=now)


async def _run_stale_upload_recovery() -> int:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    try:
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        storage = Boto3ObjectStorage(
            settings.s3_bucket,
            settings.s3_endpoint_url,
            settings.s3_access_key_id,
            settings.s3_secret_access_key,
            public_endpoint_url=settings.s3_public_endpoint_url,
        )
        return await execute_stale_upload_recovery(
            session_factory=session_factory,
            storage=storage,
        )
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
    queue="file-scan",
)
def recover_stale_uploads_task() -> int:
    return asyncio.run(_run_stale_upload_recovery())
