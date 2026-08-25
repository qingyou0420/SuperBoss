"""Creation, idempotency, and resumable provisioning contracts for K3 imports."""

import importlib
import json
from datetime import UTC, datetime
from types import ModuleType
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from superboss.core.actors import Actor
from superboss.core.errors import DomainError
from superboss.modules.audit.models import AuditLog
from superboss.modules.devices.models import (
    DeviceConnection,
    DeviceProjectGrant,
    DeviceScopeGrant,
)
from superboss.modules.files.models import File
from superboss.modules.projects.models import Project
from tests.files.storage import InMemoryObjectStorage

ALL_IMPORT_SCOPES = frozenset(
    {"imports:create", "imports:upload", "imports:submit", "imports:read-own"}
)



def import_contract() -> tuple[ModuleType, ModuleType, ModuleType]:
    """Load wished-for imports APIs lazily so RED is a normal feature failure."""
    try:
        return (
            importlib.import_module("superboss.modules.imports.models"),
            importlib.import_module("superboss.modules.imports.schemas"),
            importlib.import_module("superboss.modules.imports.service"),
        )
    except ModuleNotFoundError as error:
        pytest.fail(f"Task 10 imports module is not implemented ({error.name})")


@pytest.fixture
def session_factory(db_session: AsyncSession) -> async_sessionmaker[AsyncSession]:
    assert db_session.bind is not None
    return async_sessionmaker(db_session.bind, expire_on_commit=False)


async def seed_device_actor(
    db_session: AsyncSession,
    active_owner: Any,
    *,
    name: str,
    scopes: frozenset[str] = ALL_IMPORT_SCOPES,
) -> tuple[Project, DeviceConnection, Actor]:
    project = Project(name=name)
    device = DeviceConnection(owner_id=active_owner.id, name=f"{name} device")
    db_session.add_all([project, device])
    await db_session.flush()
    db_session.add(DeviceProjectGrant(device_id=device.id, project_id=project.id))
    db_session.add_all(
        [DeviceScopeGrant(device_id=device.id, scope=scope) for scope in sorted(scopes)]
    )
    await db_session.commit()
    return (
        project,
        device,
        Actor("device", device.id, None, frozenset({project.id}), scopes),
    )


def manifest_payload(project_id: UUID, *, two_attachments: bool = False) -> dict[str, object]:
    attachments: list[dict[str, object]] = [
        {
            "kind": "K3_RAW",
            "filename": "k3-output.json",
            "size_bytes": 128,
            "sha256": "b" * 64,
            "content_type": "application/json",
        }
    ]
    base_sha256: str | None = None
    if two_attachments:
        attachments.insert(
            0,
            {
                "kind": "ORIGINAL",
                "filename": "original.pdf",
                "size_bytes": 1024,
                "sha256": "a" * 64,
                "content_type": "application/pdf",
            },
        )
        base_sha256 = "a" * 64
    return {
        "project_id": project_id,
        "local_task_id": "local-task-001",
        "external_document_reference": "external-doc-001",
        "base_sha256": base_sha256,
        "k3_result": {
            "model_label": "K3",
            "processed_at": datetime(2026, 8, 9, 4, 30, tzinfo=UTC),
            "modification_details": ["改写了第一段"],
            "knowledge_points": ["交付标准"],
            "risks": ["需要人工复核"],
            "suggested_title": "建议标题",
            "suggested_tags": ["复核", "合同"],
        },
        "attachments": attachments,
    }


def oversized_manifest_payload(project_id: UUID) -> dict[str, object]:
    """Build valid components whose compact normalized UTF-8 aggregate exceeds 64 KiB."""
    payload = manifest_payload(project_id, two_attachments=True)
    k3_result = payload["k3_result"]
    attachments = payload["attachments"]
    assert isinstance(k3_result, dict) and isinstance(attachments, list)
    original = attachments[0]
    raw = attachments[1]
    revised = dict(original)
    revised.update(
        kind="REVISED",
        filename=f"修订稿-{'界' * 900}.pdf",
        sha256="c" * 64,
    )
    original["filename"] = f"原稿-{'界' * 900}.pdf"
    raw["filename"] = f"原始输出-{'界' * 900}.json"
    payload["attachments"] = [original, revised, raw]
    payload["local_task_id"] = "local-" + "x" * 190
    payload["external_document_reference"] = "外部-" + "界" * 900
    k3_result.update(
        model_label="模型-" + "界" * 100,
        modification_details=[f"修改-{index}-" + "界" * 300 for index in range(20)],
        knowledge_points=[f"知识-{index}-" + "界" * 300 for index in range(20)],
        risks=[f"风险-{index}-" + "界" * 300 for index in range(20)],
        suggested_title="标题-" + "界" * 900,
        suggested_tags=[f"标签-{index}-" + "界" * 64 for index in range(20)],
    )
    return payload


def compact_manifest_octets(payload: dict[str, object]) -> int:
    def encode_canonical_scalar(value: object) -> str:
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, UUID):
            return str(value)
        raise TypeError(f"Unsupported canonical manifest value: {type(value)!r}")

    return len(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=encode_canonical_scalar,
        ).encode("utf-8")
    )


def command_for(project_id: UUID, *, two_attachments: bool = False) -> Any:
    _models, schemas, _service = import_contract()
    return schemas.ImportJobCreate.model_validate(
        manifest_payload(project_id, two_attachments=two_attachments)
    )


def service_for(
    session_factory: async_sessionmaker[AsyncSession],
    storage: InMemoryObjectStorage,
    enqueue_scan: Any | None = None,
) -> Any:
    _models, _schemas, service = import_contract()
    return service.ImportService(session_factory, storage, enqueue_scan)


def returned_attachment_ids(result: Any) -> tuple[tuple[UUID, UUID, UUID], ...]:
    return tuple(
        sorted(
            (
                (attachment.id, attachment.file_id, attachment.upload_id)
                for attachment in result.attachments
            ),
            key=lambda item: str(item[0]),
        )
    )


@pytest.mark.asyncio
async def test_same_key_and_semantically_equivalent_manifest_reuse_every_stable_id(
    db_session: AsyncSession,
    active_owner: Any,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Reordered declarations and equivalent offsets must not duplicate durable work."""
    models, schemas, _service = import_contract()
    project, _device, actor = await seed_device_actor(
        db_session, active_owner, name="Equivalent import"
    )
    first_payload = manifest_payload(project.id, two_attachments=True)
    second_payload = manifest_payload(project.id, two_attachments=True)
    second_attachments = second_payload["attachments"]
    second_k3 = second_payload["k3_result"]
    assert isinstance(second_attachments, list) and isinstance(second_k3, dict)
    second_payload["attachments"] = list(reversed(second_attachments))
    second_k3["processed_at"] = "2026-08-09T12:30:00+08:00"
    first_command = schemas.ImportJobCreate.model_validate(first_payload)
    second_command = schemas.ImportJobCreate.model_validate(second_payload)
    storage = InMemoryObjectStorage()
    service = service_for(session_factory, storage)

    first = await service.create(actor, first_command, "equivalent-key", request_id=uuid4())
    replay = await service.create(actor, second_command, "equivalent-key", request_id=uuid4())

    assert replay.id == first.id
    assert returned_attachment_ids(replay) == returned_attachment_ids(first)
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(models.ImportJob)) == 1
        assert await session.scalar(select(func.count()).select_from(models.ImportAttachment)) == 2
        assert await session.scalar(select(func.count()).select_from(File)) == 2
        assert await session.scalar(select(func.count()).select_from(File)) == 2
        jobs = list(await session.scalars(select(models.ImportJob)))
        audits = list(
            await session.scalars(
                select(AuditLog).where(
                    AuditLog.action == "import.create",
                    AuditLog.object_id == first.id,
                )
            )
        )
    assert len(jobs) == 1 and len(audits) == 1
    assert len(jobs[0].manifest_fingerprint) == 64
    assert isinstance(jobs[0].canonical_manifest_json, dict)
    assert compact_manifest_octets(jobs[0].canonical_manifest_json) <= 65_536
    serialized_audit = json.dumps(audits[0].metadata_json, ensure_ascii=False)
    for raw_k3_text in ("改写了第一段", "交付标准", "需要人工复核"):
        assert raw_k3_text not in serialized_audit
    for unsafe_coordinate in (
        *(file.object_key for file in (await db_session.scalars(select(File))).all()),
        *storage.active,
    ):
        assert unsafe_coordinate not in serialized_audit
    assert storage.create_calls == 2 and len(storage.active) == 2


@pytest.mark.asyncio
async def test_same_key_with_changed_manifest_conflicts_without_mutating_the_winner(
    db_session: AsyncSession,
    active_owner: Any,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A reused key may not overwrite the accepted manifest or allocate alternate uploads."""
    models, schemas, _service = import_contract()
    project, _device, actor = await seed_device_actor(
        db_session, active_owner, name="Changed import"
    )
    winner_command = command_for(project.id)
    changed_payload = manifest_payload(project.id)
    changed_payload["local_task_id"] = "changed-local-task"
    changed_command = schemas.ImportJobCreate.model_validate(changed_payload)
    storage = InMemoryObjectStorage()
    service = service_for(session_factory, storage)
    winner = await service.create(actor, winner_command, "conflict-key", request_id=uuid4())
    before_ids = returned_attachment_ids(winner)

    with pytest.raises(DomainError) as conflict:
        await service.create(actor, changed_command, "conflict-key", request_id=uuid4())

    assert conflict.value.status_code == 409
    assert conflict.value.code == "IMPORT_IDEMPOTENCY_CONFLICT"
    async with session_factory() as session:
        saved = await session.get(models.ImportJob, winner.id)
        attachments = list(
            await session.scalars(
                select(models.ImportAttachment).where(
                    models.ImportAttachment.job_id == winner.id
                )
            )
        )
        create_audits = list(
            await session.scalars(select(AuditLog).where(AuditLog.action == "import.create"))
        )
    assert saved is not None and saved.local_task_id == "local-task-001"
    assert tuple(
        sorted(
            ((row.id, row.file_id, row.upload_id) for row in attachments),
            key=lambda item: str(item[0]),
        )
    ) == before_ids
    assert len(create_audits) == 1
    assert storage.create_calls == 1 and len(storage.active) == 1
