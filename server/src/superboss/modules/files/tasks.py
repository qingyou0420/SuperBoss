"""Celery delivery and execution boundary for quarantined file scans."""

import asyncio
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from superboss.core.config import get_settings
from superboss.infrastructure.clamav import ClamAVScanner, Scanner
from superboss.infrastructure.s3 import Boto3ObjectStorage
from superboss.modules.files.service import FileScanService
from superboss.modules.files.storage import ObjectStorage
from superboss.modules.imports.service import ImportService
from superboss.workers.celery_app import celery_app

settings = get_settings()


async def execute_file_scan(
    file_id: str,
    *,
    session_factory: async_sessionmaker[AsyncSession],
    storage: ObjectStorage,
    scanner: Scanner,
) -> None:
    parsed_file_id = UUID(file_id)
    await FileScanService(session_factory, storage, scanner).scan_file(parsed_file_id)
    await ImportService(session_factory, storage).reconcile_file(parsed_file_id)


async def _run_scan_file(file_id: str) -> None:
    UUID(file_id)
    active_settings = get_settings()
    engine = create_async_engine(active_settings.database_url, pool_pre_ping=True)
    try:
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        storage = Boto3ObjectStorage(
            active_settings.s3_bucket,
            active_settings.s3_endpoint_url,
            active_settings.s3_access_key_id,
            active_settings.s3_secret_access_key,
        )
        scanner = ClamAVScanner(
            host=active_settings.clamav_host,
            port=active_settings.clamav_port,
            connect_timeout_seconds=active_settings.clamav_connect_timeout_seconds,
            io_timeout_seconds=active_settings.clamav_io_timeout_seconds,
            total_timeout_seconds=active_settings.clamav_total_timeout_seconds,
            max_chunk_bytes=active_settings.clamav_max_chunk_bytes,
            max_stream_bytes=active_settings.clamav_max_stream_bytes,
            max_response_bytes=active_settings.clamav_max_response_bytes,
        )
        await execute_file_scan(
            file_id,
            session_factory=session_factory,
            storage=storage,
            scanner=scanner,
        )
    finally:
        await engine.dispose()


@celery_app.task(  # type: ignore[untyped-decorator]
    name="superboss.files.scan",
    acks_late=True,
    autoretry_for=(Exception,),
    dont_autoretry_for=(ValueError,),
    max_retries=3,
    reject_on_worker_lost=True,
    retry_backoff=True,
    retry_backoff_max=60,
    retry_kwargs={"max_retries": 3},
    soft_time_limit=settings.scan_soft_time_limit_seconds,
    time_limit=settings.scan_hard_time_limit_seconds,
    queue="file-scan",
)
def scan_file_task(file_id: str) -> None:
    asyncio.run(_run_scan_file(file_id))


def enqueue_file_scan(file_id: UUID, delivery_key: UUID) -> None:
    scan_file_task.apply_async(
        args=[str(file_id)],
        task_id=str(delivery_key),
        queue="file-scan",
    )
