"""Celery application configuration with no broker connection at import time."""

from celery import Celery  # type: ignore[import-untyped]

from superboss.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "superboss",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["superboss.modules.files.tasks"],
)
celery_app.conf.update(
    accept_content=["json"],
    broker_connection_retry_on_startup=True,
    result_serializer="json",
    task_acks_late=True,
    task_default_queue="file-scan",
    task_reject_on_worker_lost=True,
    task_routes={"superboss.files.scan": {"queue": "file-scan"}},
    task_serializer="json",
    timezone="UTC",
    worker_concurrency=1,
    worker_prefetch_multiplier=1,
)
