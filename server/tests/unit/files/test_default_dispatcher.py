"""Default scan-dispatcher production wiring."""

from uuid import uuid4

from superboss.core.config import Settings
from superboss.main import create_app


def test_default_file_scan_dispatcher_uses_stable_celery_delivery(
    monkeypatch,
    test_settings: Settings,
) -> None:
    """Leaving the old unconfigured default would strand durable scan outbox work."""
    from superboss.modules.files import tasks

    sent: list[dict[str, object]] = []

    def apply_async(**options: object) -> None:
        sent.append(options)

    monkeypatch.setattr(tasks.scan_file_task, "apply_async", apply_async)
    app = create_app(test_settings)
    file_id = uuid4()
    delivery_key = uuid4()

    app.state.enqueue_file_scan(file_id, delivery_key)

    assert sent == [
        {
            "args": [str(file_id)],
            "task_id": str(delivery_key),
            "queue": "file-scan",
        }
    ]
