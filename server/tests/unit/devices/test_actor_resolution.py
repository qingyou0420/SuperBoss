"""Shared Actor integration for device bearer credentials."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.requests import Request

from superboss.core.actors import get_actor
from superboss.core.config import Settings
from superboss.modules.audit.models import AuditLog
from superboss.modules.devices.models import DeviceConnection
from superboss.modules.devices.service import DeviceService
from superboss.modules.projects.models import Project
from superboss.modules.users.models import User


@pytest.mark.asyncio
async def test_get_actor_resolves_device_from_live_database_state(
    db_session: AsyncSession, active_owner: User, test_settings: Settings
) -> None:
    """Decoding every Bearer as a browser JWT would make device auth unusable or privileged."""
    project = Project(name="Actor device")
    db_session.add(project)
    await db_session.commit()
    assert db_session.bind is not None
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    service = DeviceService(factory, test_settings)
    code = await service.create_pairing_code(active_owner.id, [project.id], request_id=uuid4())
    pair = await service.pair(code.raw_code, "Actor-PC", request_id=uuid4())
    request_id = uuid4()
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/device-auth/me",
            "headers": [(b"authorization", f"Bearer {pair.access_token}".encode())],
            "app": SimpleNamespace(
                state=SimpleNamespace(session_factory=factory, settings=test_settings)
            ),
            "state": {"request_id": str(request_id)},
        }
    )

    actor = await get_actor(request)

    assert actor.kind == "device" and actor.role is None
    assert actor.subject_id == pair.device_id
    assert actor.project_ids == frozenset({project.id})
    assert actor.scopes == frozenset(
        {"imports:create", "imports:read-own", "imports:submit", "imports:upload"}
    )
    async with factory() as session:
        device = await session.get(DeviceConnection, pair.device_id)
        use_event = await session.scalar(
            select(AuditLog).where(
                AuditLog.action == "device.use", AuditLog.request_id == request_id
            )
        )
    assert device is not None and device.last_used_at is not None
    assert use_event is not None and use_event.actor_id == pair.device_id
