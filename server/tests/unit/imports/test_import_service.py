"""Creation, idempotency, and resumable provisioning contracts for K3 imports."""

import asyncio
import importlib
import json
from datetime import UTC, datetime, timedelta
from types import ModuleType
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import event, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from superboss.core.actors import Actor
from superboss.core.errors import DomainError
from superboss.modules.audit.models import AuditLog
from superboss.modules.devices.models import (
    DeviceConnection,
    DeviceProjectGrant,
    DeviceScopeGrant,
)
from superboss.modules.files.models import (
    File,
    FileLifecycleOutbox,
    FileState,
    FileUploadLifecycle,
    Upload,
)
from superboss.modules.files.service import FileLifecycleService, FileService
from superboss.modules.files.storage import CompletedPart
from superboss.modules.projects.models import Project
from superboss.modules.users.models import Role
from tests.files.storage import InMemoryObjectStorage

ALL_IMPORT_SCOPES = frozenset(
    {"imports:create", "imports:upload", "imports:submit", "imports:read-own"}
)


class ProbeTrackingStorage(InMemoryObjectStorage):
    """Count every storage call reachable after an import attachment has been provisioned."""

    def __init__(self, *, complete_size: int = 1) -> None:
        super().__init__(complete_size=complete_size)
        self.probe_calls: list[str] = []

    async def presign_upload_part(
        self,
        object_key: str,
        multipart_id: str,
        part_number: int,
        expires_seconds: int,
    ) -> str:
        self.probe_calls.append("presign")
        return await super().presign_upload_part(
            object_key,
            multipart_id,
            part_number,
            expires_seconds,
        )

    async def stat_object(self, object_key: str) -> Any:
        self.probe_calls.append("stat")
        return await super().stat_object(object_key)

    async def complete_multipart(
        self,
        object_key: str,
        multipart_id: str,
        parts: list[CompletedPart],
    ) -> Any:
        self.probe_calls.append("complete")
        return await super().complete_multipart(object_key, multipart_id, parts)


class FirstMultipartCallBarrierStorage(InMemoryObjectStorage):
    """Hold only the first provider create while a changed request competes."""

    def __init__(self) -> None:
        super().__init__()
        self.first_call_entered = asyncio.Event()
        self.followup_call_entered = asyncio.Event()
        self.release_first_call = asyncio.Event()

    async def create_multipart(self, object_key: str, content_type: str) -> str:
        multipart_id = await super().create_multipart(object_key, content_type)
        if self.create_calls == 1:
            self.first_call_entered.set()
            await self.release_first_call.wait()
        else:
            self.followup_call_entered.set()
        return multipart_id


async def wait_for_advisory_lock_contender(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Let a future serialized implementation release the one-sided provider gate."""
    while True:
        async with session_factory() as session:
            waiting = await session.scalar(
                text(
                    "SELECT EXISTS ("
                    "SELECT 1 FROM pg_locks "
                    "WHERE locktype = 'advisory' AND NOT granted)"
                )
            )
        if waiting:
            return
        await asyncio.sleep(0.01)


async def advisory_lock_count(
    session_factory: async_sessionmaker[AsyncSession],
) -> int:
    """Count database-scoped advisory locks after a failed create unwinds."""
    async with session_factory() as session:
        count = await session.scalar(
            text(
                "SELECT count(*) FROM pg_locks "
                "WHERE locktype = 'advisory' "
                "AND database = (SELECT oid FROM pg_database WHERE datname = current_database())"
            )
        )
    assert count is not None
    return count


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
    session_factory: async_sessionmaker[AsyncSession], storage: InMemoryObjectStorage
) -> Any:
    _models, _schemas, service = import_contract()
    return service.ImportService(session_factory, storage)


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
        assert await session.scalar(select(func.count()).select_from(Upload)) == 2
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


@pytest.mark.asyncio
async def test_concurrent_same_manifest_has_one_job_attachment_file_and_upload_set(
    db_session: AsyncSession,
    active_owner: Any,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Independent connector retries must converge at the database uniqueness boundary."""
    models, _schemas, _service = import_contract()
    project, _device, actor = await seed_device_actor(
        db_session, active_owner, name="Concurrent import"
    )
    command = command_for(project.id)
    storage = InMemoryObjectStorage()
    barrier = asyncio.Barrier(2)

    async def create() -> Any:
        await barrier.wait()
        return await service_for(session_factory, storage).create(
            actor, command, "concurrent-key", request_id=uuid4()
        )

    first, second = await asyncio.wait_for(asyncio.gather(create(), create()), timeout=10)

    assert first.id == second.id
    assert returned_attachment_ids(first) == returned_attachment_ids(second)
    async with session_factory() as session:
        counts = (
            await session.scalar(select(func.count()).select_from(models.ImportJob)),
            await session.scalar(select(func.count()).select_from(models.ImportAttachment)),
            await session.scalar(select(func.count()).select_from(File)),
            await session.scalar(select(func.count()).select_from(Upload)),
            await session.scalar(
                select(func.count()).select_from(AuditLog).where(
                    AuditLog.action == "import.create"
                )
            ),
        )
    assert counts == (1, 1, 1, 1, 1)
    assert storage.create_calls == 1 and len(storage.active) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("race_round", range(3))
@pytest.mark.parametrize(
    "variant",
    ("different_projects", "same_project_different_kinds"),
)
async def test_concurrent_changed_manifest_has_no_losing_upload_set(
    db_session: AsyncSession,
    active_owner: Any,
    session_factory: async_sessionmaker[AsyncSession],
    variant: str,
    race_round: int,
) -> None:
    """The parent key must bind before either changed request provisions children."""
    models, _schemas, _service = import_contract()
    first_project, device, actor = await seed_device_actor(
        db_session,
        active_owner,
        name=f"Changed manifest race {variant} {race_round}",
    )
    if variant == "different_projects":
        second_project = Project(name=f"Changed race second project {race_round}")
        db_session.add(second_project)
        await db_session.flush()
        db_session.add(
            DeviceProjectGrant(device_id=device.id, project_id=second_project.id)
        )
        await db_session.commit()
        actor = Actor(
            "device",
            device.id,
            None,
            frozenset({first_project.id, second_project.id}),
            ALL_IMPORT_SCOPES,
        )
        first_command = command_for(first_project.id)
        second_command = command_for(second_project.id)
    else:
        first_command = command_for(first_project.id, two_attachments=True)
        second_command = command_for(first_project.id)

    storage = FirstMultipartCallBarrierStorage()
    key = f"changed-race-{variant}-{race_round}"

    async def create(command: Any) -> Any:
        return await service_for(session_factory, storage).create(
            actor,
            command,
            key,
            request_id=uuid4(),
        )

    async def race() -> tuple[Any, Any]:
        first_task = asyncio.create_task(create(first_command))
        await storage.first_call_entered.wait()
        second_task = asyncio.create_task(create(second_command))
        provider_contender = asyncio.create_task(storage.followup_call_entered.wait())
        advisory_contender = asyncio.create_task(
            wait_for_advisory_lock_contender(session_factory)
        )
        try:
            ready, _pending = await asyncio.wait(
                {second_task, provider_contender, advisory_contender},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if provider_contender in ready:
                await second_task
            storage.release_first_call.set()
            first, second = await asyncio.gather(
                first_task,
                second_task,
                return_exceptions=True,
            )
            return first, second
        finally:
            storage.release_first_call.set()
            for waiter in (provider_contender, advisory_contender):
                waiter.cancel()
            await asyncio.gather(
                provider_contender,
                advisory_contender,
                return_exceptions=True,
            )

    first, second = await asyncio.wait_for(race(), timeout=10)
    results = (first, second)
    successes = [result for result in results if not isinstance(result, BaseException)]
    conflicts = [result for result in results if isinstance(result, DomainError)]
    assert len(successes) == 1
    assert len(conflicts) == 1
    assert conflicts[0].status_code == 409
    assert conflicts[0].code == "IMPORT_IDEMPOTENCY_CONFLICT"
    winner = successes[0]
    expected_children = len(winner.attachments)

    async with session_factory() as session:
        jobs = list(await session.scalars(select(models.ImportJob)))
        attachments = list(await session.scalars(select(models.ImportAttachment)))
        files = list(await session.scalars(select(File)))
        uploads = list(await session.scalars(select(Upload)))
        lifecycles = list(await session.scalars(select(FileUploadLifecycle)))
        audits = list(
            await session.scalars(
                select(AuditLog).where(AuditLog.action == "import.create")
            )
        )

    assert len(jobs) == len(audits) == 1
    assert jobs[0].id == winner.id
    assert len(attachments) == len(files) == len(uploads) == len(lifecycles) == (
        expected_children
    )
    assert {row.file_id for row in attachments} == {row.id for row in files}
    assert {row.upload_id for row in attachments} == {row.id for row in uploads}
    assert {row.multipart_id for row in uploads} == set(storage.active)
    assert storage.create_calls == expected_children


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_mode", ("cancel", "exception"))
@pytest.mark.parametrize(
    "variant",
    ("different_projects", "same_project_different_kinds"),
)
async def test_changed_manifest_cannot_take_key_after_provider_before_parent_failure(
    db_session: AsyncSession,
    active_owner: Any,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    variant: str,
    failure_mode: str,
) -> None:
    """A durable fingerprint must survive cancellation/crash after child provisioning."""
    models, _schemas, _service = import_contract()
    first_project, device, actor = await seed_device_actor(
        db_session,
        active_owner,
        name=f"Durable claim {variant} {failure_mode}",
    )
    if variant == "different_projects":
        changed_project = Project(name=f"Durable claim changed {failure_mode}")
        db_session.add(changed_project)
        await db_session.flush()
        db_session.add(
            DeviceProjectGrant(device_id=device.id, project_id=changed_project.id)
        )
        await db_session.commit()
        actor = Actor(
            "device",
            device.id,
            None,
            frozenset({first_project.id, changed_project.id}),
            ALL_IMPORT_SCOPES,
        )
        original_command = command_for(first_project.id)
        changed_command = command_for(changed_project.id)
    else:
        original_command = command_for(first_project.id, two_attachments=True)
        changed_command = command_for(first_project.id)

    storage = InMemoryObjectStorage()
    first_service = service_for(session_factory, storage)
    original_persist = first_service._persist_job
    before_parent = asyncio.Event()
    release_parent = asyncio.Event()

    async def pause_before_parent(*args: Any, **kwargs: Any) -> Any:
        before_parent.set()
        await release_parent.wait()
        if failure_mode == "exception":
            raise RuntimeError("post-provider pre-parent failure")
        return await original_persist(*args, **kwargs)

    monkeypatch.setattr(first_service, "_persist_job", pause_before_parent)
    key = f"durable-claim-{variant}-{failure_mode}"
    first_task = asyncio.create_task(
        first_service.create(actor, original_command, key, request_id=uuid4())
    )
    changed_task: asyncio.Task[Any] | None = None
    advisory_contender: asyncio.Task[None] | None = None
    try:
        await asyncio.wait_for(before_parent.wait(), timeout=5)
        expected_children = len(original_command.attachments)
        async with session_factory() as session:
            assert await session.scalar(select(func.count()).select_from(models.ImportJob)) == 0
            assert await session.scalar(
                select(func.count()).select_from(models.ImportAttachment)
            ) == 0
            assert await session.scalar(select(func.count()).select_from(File)) == expected_children
            assert await session.scalar(select(func.count()).select_from(Upload)) == expected_children
            assert await session.scalar(
                select(func.count()).select_from(FileUploadLifecycle)
            ) == expected_children
            claims = list(
                await session.scalars(select(models.ImportIdempotencyClaim))
            )
            assert len(claims) == 1
            assert claims[0].device_id == device.id
            assert claims[0].idempotency_key == key
        assert storage.create_calls == expected_children

        changed_task = asyncio.create_task(
            service_for(session_factory, storage).create(
                actor,
                changed_command,
                key,
                request_id=uuid4(),
            )
        )
        advisory_contender = asyncio.create_task(
            wait_for_advisory_lock_contender(session_factory)
        )
        ready, _pending = await asyncio.wait(
            {changed_task, advisory_contender},
            timeout=5,
            return_when=asyncio.FIRST_COMPLETED,
        )
        assert ready, "changed create neither rejected a durable claim nor reached the old lock"

        if failure_mode == "cancel":
            first_task.cancel()
        else:
            release_parent.set()
        first_result = (await asyncio.gather(first_task, return_exceptions=True))[0]
        changed_result = (
            await asyncio.wait_for(
                asyncio.gather(changed_task, return_exceptions=True),
                timeout=5,
            )
        )[0]
    finally:
        release_parent.set()
        if not first_task.done():
            first_task.cancel()
        if changed_task is not None and not changed_task.done():
            changed_task.cancel()
        if advisory_contender is not None:
            advisory_contender.cancel()
        await asyncio.gather(
            *(task for task in (first_task, changed_task, advisory_contender) if task is not None),
            return_exceptions=True,
        )

    if failure_mode == "cancel":
        assert isinstance(first_result, asyncio.CancelledError)
    else:
        assert isinstance(first_result, RuntimeError)
        assert str(first_result) == "post-provider pre-parent failure"
    assert isinstance(changed_result, DomainError)
    assert changed_result.status_code == 409
    assert changed_result.code == "IMPORT_IDEMPOTENCY_CONFLICT"

    identical = await service_for(session_factory, storage).create(
        actor,
        original_command,
        key,
        request_id=uuid4(),
    )
    expected_children = len(original_command.attachments)
    async with session_factory() as session:
        jobs = list(await session.scalars(select(models.ImportJob)))
        attachments = list(await session.scalars(select(models.ImportAttachment)))
        files = list(await session.scalars(select(File)))
        uploads = list(await session.scalars(select(Upload)))
        lifecycles = list(await session.scalars(select(FileUploadLifecycle)))
        audits = list(
            await session.scalars(
                select(AuditLog).where(AuditLog.action == "import.create")
            )
        )
        claims = list(await session.scalars(select(models.ImportIdempotencyClaim)))
    assert len(jobs) == len(audits) == 1 and jobs[0].id == identical.id
    assert len(claims) == 1 and claims[0].idempotency_key == key
    assert len(attachments) == len(identical.attachments) == expected_children
    assert len(files) == len(uploads) == len(lifecycles) == expected_children
    assert {row.file_id for row in attachments} == {row.id for row in files}
    assert {row.upload_id for row in attachments} == {row.id for row in uploads}
    assert {row.project_id for row in files} == {first_project.id}
    assert {row.multipart_id for row in uploads} == set(storage.active)
    assert storage.create_calls == expected_children


@pytest.mark.asyncio
async def test_distinct_keys_do_not_starve_two_connection_pool_at_child_boundary(
    db_session: AsyncSession,
    active_owner: Any,
    session_factory: async_sessionmaker[AsyncSession],
    postgres_database: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Claiming two keys may not reserve both connections before child DB work."""
    models, _schemas, _service = import_contract()
    project, _device, actor = await seed_device_actor(
        db_session,
        active_owner,
        name="Two connection distinct keys",
    )
    storage = InMemoryObjectStorage()
    child_boundary = asyncio.Barrier(2)
    original_start = FileService.start_import_upload

    async def synchronize_child_db(
        file_service: FileService,
        child_actor: Actor,
        child_command: Any,
        child_key: str,
    ) -> Upload:
        await child_boundary.wait()
        return await original_start(
            file_service,
            child_actor,
            child_command,
            child_key,
        )

    monkeypatch.setattr(FileService, "start_import_upload", synchronize_child_db)
    limited_engine = create_async_engine(
        postgres_database,
        pool_size=2,
        max_overflow=0,
        pool_timeout=0.25,
    )
    limited_factory = async_sessionmaker(limited_engine, expire_on_commit=False)
    try:
        service = service_for(limited_factory, storage)
        results = await asyncio.wait_for(
            asyncio.gather(
                service.create(actor, command_for(project.id), "pool-distinct-a", request_id=uuid4()),
                service.create(actor, command_for(project.id), "pool-distinct-b", request_id=uuid4()),
                return_exceptions=True,
            ),
            timeout=5,
        )
        checked_out = limited_engine.pool.checkedout()
    finally:
        await limited_engine.dispose()

    assert not [result for result in results if isinstance(result, BaseException)]
    first, second = results
    assert first.id != second.id
    assert checked_out == 0
    async with session_factory() as session:
        counts = (
            await session.scalar(select(func.count()).select_from(models.ImportJob)),
            await session.scalar(select(func.count()).select_from(models.ImportAttachment)),
            await session.scalar(select(func.count()).select_from(File)),
            await session.scalar(select(func.count()).select_from(Upload)),
            await session.scalar(select(func.count()).select_from(FileUploadLifecycle)),
        )
    assert counts == (2, 2, 2, 2, 2)
    assert storage.create_calls == 2 and len(storage.active) == 2


@pytest.mark.asyncio
async def test_identical_same_key_waiter_does_not_starve_winners_child_connection(
    db_session: AsyncSession,
    active_owner: Any,
    session_factory: async_sessionmaker[AsyncSession],
    postgres_database: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A same-key waiter must not occupy the connection its winner needs for FileService."""
    project, _device, actor = await seed_device_actor(
        db_session,
        active_owner,
        name="Two connection same key",
    )
    command = command_for(project.id)
    storage = InMemoryObjectStorage()
    first_child = asyncio.Event()
    second_child = asyncio.Event()
    release_children = asyncio.Event()
    child_calls = 0
    original_start = FileService.start_import_upload

    async def synchronize_same_key_children(
        file_service: FileService,
        child_actor: Actor,
        child_command: Any,
        child_key: str,
    ) -> Upload:
        nonlocal child_calls
        child_calls += 1
        if child_calls == 1:
            first_child.set()
            await release_children.wait()
        else:
            second_child.set()
            release_children.set()
        return await original_start(
            file_service,
            child_actor,
            child_command,
            child_key,
        )

    monkeypatch.setattr(
        FileService,
        "start_import_upload",
        synchronize_same_key_children,
    )
    limited_engine = create_async_engine(
        postgres_database,
        pool_size=2,
        max_overflow=0,
        pool_timeout=0.25,
    )
    limited_factory = async_sessionmaker(limited_engine, expire_on_commit=False)
    first_task: asyncio.Task[Any] | None = None
    second_task: asyncio.Task[Any] | None = None
    second_child_observer: asyncio.Task[bool] | None = None
    old_lock_observer: asyncio.Task[None] | None = None
    try:
        service = service_for(limited_factory, storage)
        first_task = asyncio.create_task(
            service.create(actor, command, "pool-identical", request_id=uuid4())
        )
        await asyncio.wait_for(first_child.wait(), timeout=2)
        second_task = asyncio.create_task(
            service.create(actor, command, "pool-identical", request_id=uuid4())
        )
        old_lock_observer = asyncio.create_task(
            wait_for_advisory_lock_contender(session_factory)
        )
        second_child_observer = asyncio.create_task(second_child.wait())
        ready, _pending = await asyncio.wait(
            {
                second_child_observer,
                old_lock_observer,
            },
            timeout=2,
            return_when=asyncio.FIRST_COMPLETED,
        )
        assert ready, "waiter reached neither durable replay nor the old advisory wait"
        release_children.set()
        results = await asyncio.wait_for(
            asyncio.gather(first_task, second_task, return_exceptions=True),
            timeout=5,
        )
        checked_out = limited_engine.pool.checkedout()
    finally:
        release_children.set()
        for task in (
            first_task,
            second_task,
            second_child_observer,
            old_lock_observer,
        ):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(
                task
                for task in (
                    first_task,
                    second_task,
                    second_child_observer,
                    old_lock_observer,
                )
                if task is not None
            ),
            return_exceptions=True,
        )
        await limited_engine.dispose()

    assert not [result for result in results if isinstance(result, BaseException)]
    first, second = results
    assert first.id == second.id
    assert returned_attachment_ids(first) == returned_attachment_ids(second)
    assert checked_out == 0
    assert storage.create_calls == 1 and len(storage.active) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_mode", ("cancel", "provider", "audit"))
async def test_failed_create_releases_small_pool_and_lock_ownership_for_retry(
    db_session: AsyncSession,
    active_owner: Any,
    session_factory: async_sessionmaker[AsyncSession],
    postgres_database: str,
    failure_mode: str,
) -> None:
    """Cancellation/provider/audit faults retain only durable reusable state, never resources."""
    models, _schemas, _service = import_contract()

    class FirstCreateFaultStorage(InMemoryObjectStorage):
        def __init__(self) -> None:
            super().__init__()
            self.provider_created = asyncio.Event()
            self.release_provider = asyncio.Event()
            self.faulted = False

        async def create_multipart(self, object_key: str, content_type: str) -> str:
            multipart_id = await super().create_multipart(object_key, content_type)
            if not self.faulted:
                self.faulted = True
                self.provider_created.set()
                if failure_mode == "cancel":
                    await self.release_provider.wait()
                elif failure_mode == "provider":
                    raise RuntimeError("provider response failed")
            return multipart_id

    project, _device, actor = await seed_device_actor(
        db_session,
        active_owner,
        name=f"Two connection fault {failure_mode}",
    )
    command = command_for(project.id)
    storage = FirstCreateFaultStorage()
    limited_engine = create_async_engine(
        postgres_database,
        pool_size=2,
        max_overflow=0,
        pool_timeout=0.25,
    )
    limited_factory = async_sessionmaker(limited_engine, expire_on_commit=False)
    audit_listener_installed = False

    def fail_success_audit(
        _mapper: object,
        _connection: object,
        target: AuditLog,
    ) -> None:
        if target.action == "import.create":
            raise RuntimeError("audit insert failed")

    try:
        service = service_for(limited_factory, storage)
        if failure_mode == "audit":
            event.listen(AuditLog, "before_insert", fail_success_audit)
            audit_listener_installed = True
        create_task = asyncio.create_task(
            service.create(
                actor,
                command,
                f"pool-fault-{failure_mode}",
                request_id=uuid4(),
            )
        )
        if failure_mode == "cancel":
            await asyncio.wait_for(storage.provider_created.wait(), timeout=2)
            create_task.cancel()
            storage.release_provider.set()
        first_result = (
            await asyncio.wait_for(
                asyncio.gather(create_task, return_exceptions=True),
                timeout=5,
            )
        )[0]
        if audit_listener_installed:
            event.remove(AuditLog, "before_insert", fail_success_audit)
            audit_listener_installed = False

        assert limited_engine.pool.checkedout() == 0
        assert await advisory_lock_count(session_factory) == 0
        async with session_factory() as session:
            claims_after_failure = await session.scalar(
                select(func.count()).select_from(models.ImportIdempotencyClaim)
            )
        assert claims_after_failure == 1
        if failure_mode == "cancel":
            assert isinstance(first_result, asyncio.CancelledError)
        elif failure_mode == "provider":
            assert isinstance(first_result, DomainError)
            assert first_result.code == "FILE_PROVISIONING_PENDING"
        else:
            assert isinstance(first_result, RuntimeError)
            assert str(first_result) == "audit insert failed"

        recovered = await service.create(
            actor,
            command,
            f"pool-fault-{failure_mode}",
            request_id=uuid4(),
        )
        assert limited_engine.pool.checkedout() == 0
        assert await advisory_lock_count(session_factory) == 0
    finally:
        storage.release_provider.set()
        if audit_listener_installed:
            event.remove(AuditLog, "before_insert", fail_success_audit)
        await limited_engine.dispose()

    async with session_factory() as session:
        counts = (
            await session.scalar(select(func.count()).select_from(models.ImportJob)),
            await session.scalar(select(func.count()).select_from(models.ImportAttachment)),
            await session.scalar(select(func.count()).select_from(File)),
            await session.scalar(select(func.count()).select_from(Upload)),
            await session.scalar(select(func.count()).select_from(FileUploadLifecycle)),
            await session.scalar(
                select(func.count()).select_from(AuditLog).where(
                    AuditLog.action == "import.create"
                )
            ),
            await session.scalar(
                select(func.count()).select_from(models.ImportIdempotencyClaim)
            ),
        )
    assert recovered.id is not None
    assert counts == (1, 1, 1, 1, 1, 1, 1)
    assert storage.create_calls == 1 and len(storage.active) == 1


@pytest.mark.asyncio
async def test_partial_provider_failure_reuses_deterministic_child_uploads_on_retry(
    db_session: AsyncSession,
    active_owner: Any,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A lost multipart response must leave tracked intent and never allocate a third session."""
    models, _schemas, _service = import_contract()

    class LoseSecondResponse(InMemoryObjectStorage):
        lost = False

        async def create_multipart(self, object_key: str, content_type: str) -> str:
            multipart_id = await super().create_multipart(object_key, content_type)
            if self.create_calls == 2 and not self.lost:
                self.lost = True
                raise RuntimeError("provider secret must not escape")
            return multipart_id

    project, _device, actor = await seed_device_actor(
        db_session, active_owner, name="Partial import"
    )
    command = command_for(project.id, two_attachments=True)
    storage = LoseSecondResponse()
    service = service_for(session_factory, storage)

    with pytest.raises(DomainError) as pending:
        await service.create(actor, command, "partial-key", request_id=uuid4())

    assert pending.value.status_code == 503
    assert "secret" not in str(pending.value).lower()
    async with session_factory() as session:
        before_uploads = list((await session.scalars(select(Upload).order_by(Upload.id))).all())
        before_files = list((await session.scalars(select(File).order_by(File.id))).all())
        before_lifecycles = list(
            (await session.scalars(select(FileUploadLifecycle))).all()
        )
        assert await session.scalar(select(func.count()).select_from(models.ImportJob)) == 0
        assert await session.scalar(select(func.count()).select_from(AuditLog).where(
            AuditLog.action == "import.create"
        )) == 0
    assert len(before_uploads) == len(before_files) == len(before_lifecycles) == 2
    before_ids = {(row.id, row.file_id) for row in before_uploads}
    assert storage.create_calls == 2 and len(storage.active) == 2

    created = await service.create(actor, command, "partial-key", request_id=uuid4())

    async with session_factory() as session:
        after_uploads = list((await session.scalars(select(Upload))).all())
        jobs = list((await session.scalars(select(models.ImportJob))).all())
        attachments = list((await session.scalars(select(models.ImportAttachment))).all())
    assert {(row.id, row.file_id) for row in after_uploads} == before_ids
    assert len(jobs) == 1 and jobs[0].id == created.id and len(attachments) == 2
    assert storage.create_calls == 2
    tracked_multiparts = {row.multipart_id for row in after_uploads}
    assert None not in tracked_multiparts and set(storage.active) == tracked_multiparts


@pytest.mark.asyncio
async def test_creation_audit_failure_rolls_back_job_but_preserves_reusable_uploads(
    db_session: AsyncSession,
    active_owner: Any,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Mandatory evidence and the new job must commit atomically after provisioning."""
    models, _schemas, _service = import_contract()
    project, _device, actor = await seed_device_actor(
        db_session, active_owner, name="Create audit rollback"
    )
    command = command_for(project.id, two_attachments=True)
    storage = InMemoryObjectStorage()
    service = service_for(session_factory, storage)

    def fail_create(_mapper: object, _connection: object, target: AuditLog) -> None:
        if target.action == "import.create":
            raise RuntimeError("audit unavailable")

    event.listen(AuditLog, "before_insert", fail_create)
    try:
        with pytest.raises(RuntimeError, match="audit unavailable"):
            await service.create(actor, command, "audit-key", request_id=uuid4())
    finally:
        event.remove(AuditLog, "before_insert", fail_create)

    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(models.ImportJob)) == 0
        assert await session.scalar(select(func.count()).select_from(models.ImportAttachment)) == 0
        uploads_before = list((await session.scalars(select(Upload))).all())
        assert len(uploads_before) == 2
    stable_ids = {(row.id, row.file_id) for row in uploads_before}
    assert storage.create_calls == 2 and len(storage.active) == 2

    created = await service.create(actor, command, "audit-key", request_id=uuid4())

    async with session_factory() as session:
        uploads_after = list((await session.scalars(select(Upload))).all())
        create_audits = list(
            await session.scalars(select(AuditLog).where(AuditLog.action == "import.create"))
        )
    assert created.id is not None
    assert {(row.id, row.file_id) for row in uploads_after} == stable_ids
    assert storage.create_calls == 2 and len(create_audits) == 1


@pytest.mark.asyncio
async def test_oversized_canonical_manifest_returns_422_before_any_side_effect(
    db_session: AsyncSession,
    active_owner: Any,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Aggregate UTF-8 bytes, not individually legal fields, must enforce the 64 KiB cap."""
    models, schemas, _service = import_contract()
    project, _device, actor = await seed_device_actor(
        db_session, active_owner, name="Oversized canonical manifest"
    )
    aggregate = oversized_manifest_payload(project.id)
    aggregate_k3 = aggregate["k3_result"]
    assert isinstance(aggregate_k3, dict)

    component_cases: list[tuple[str, object]] = [
        ("local_task_id", aggregate["local_task_id"]),
        (
            "external_document_reference",
            aggregate["external_document_reference"],
        ),
        ("attachments", aggregate["attachments"]),
        *(
            (field, aggregate_k3[field])
            for field in (
                "model_label",
                "modification_details",
                "knowledge_points",
                "risks",
                "suggested_title",
                "suggested_tags",
            )
        ),
    ]
    for field, value in component_cases:
        component = manifest_payload(project.id, two_attachments=True)
        if field in {"local_task_id", "external_document_reference", "attachments"}:
            component[field] = value
        else:
            component_k3 = component["k3_result"]
            assert isinstance(component_k3, dict)
            component_k3[field] = value
        assert compact_manifest_octets(component) < 65_536
        schemas.ImportJobCreate.model_validate(component)

    assert compact_manifest_octets(aggregate) > 65_536
    storage = InMemoryObjectStorage()
    status_code: int
    try:
        command = schemas.ImportJobCreate.model_validate(aggregate)
    except ValidationError:
        status_code = 422
    else:
        with pytest.raises(DomainError) as oversized:
            await service_for(session_factory, storage).create(
                actor,
                command,
                "oversized-manifest",
                request_id=uuid4(),
            )
        status_code = oversized.value.status_code
        assert oversized.value.code == "IMPORT_MANIFEST_TOO_LARGE"

    assert status_code == 422
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(models.ImportJob)) == 0
        assert await session.scalar(select(func.count()).select_from(File)) == 0
        assert await session.scalar(select(func.count()).select_from(Upload)) == 0
        assert await session.scalar(select(func.count()).select_from(AuditLog)) == 0
    assert storage.create_calls == storage.complete_calls == 0
    assert storage.active == {} and storage.expiries == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "idempotency_key",
    ["", "x" * 256, "contains space", "中文", "header\r\ninjection"],
)
async def test_invalid_idempotency_key_fails_before_database_or_storage_side_effects(
    db_session: AsyncSession,
    active_owner: Any,
    session_factory: async_sessionmaker[AsyncSession],
    idempotency_key: str,
) -> None:
    """Malformed header material must not become an upload child key or durable row."""
    models, _schemas, _service = import_contract()
    project, _device, actor = await seed_device_actor(
        db_session, active_owner, name=f"Invalid key {uuid4()}"
    )
    storage = InMemoryObjectStorage()

    with pytest.raises((ValueError, DomainError)):
        await service_for(session_factory, storage).create(
            actor,
            command_for(project.id),
            idempotency_key,
            request_id=uuid4(),
        )

    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(models.ImportJob)) == 0
        assert await session.scalar(select(func.count()).select_from(File)) == 0
    assert storage.create_calls == 0 and storage.active == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("actor_case", ["browser", "device_role", "scope", "project"])
async def test_create_requires_an_exact_device_actor_scope_and_current_project(
    db_session: AsyncSession,
    active_owner: Any,
    session_factory: async_sessionmaker[AsyncSession],
    actor_case: str,
) -> None:
    """The import service must not turn its internal FileService path into generic device access."""
    models, _schemas, _service = import_contract()
    project, device, actor = await seed_device_actor(
        db_session, active_owner, name=f"Create authorization {actor_case}"
    )
    if actor_case == "browser":
        denied_actor = Actor("user", active_owner.id, Role.OWNER, frozenset(), frozenset())
    elif actor_case == "device_role":
        denied_actor = Actor(
            "device", device.id, Role.OWNER, actor.project_ids, actor.scopes
        )
    elif actor_case == "scope":
        denied_actor = Actor(
            "device",
            device.id,
            None,
            actor.project_ids,
            frozenset(actor.scopes - {"imports:create"}),
        )
    else:
        denied_actor = Actor("device", device.id, None, frozenset(), actor.scopes)
    storage = InMemoryObjectStorage()
    command = command_for(project.id)

    with pytest.raises(DomainError) as denied:
        await service_for(session_factory, storage).create(
            denied_actor,
            command,
            f"denied-{actor_case}",
            request_id=uuid4(),
        )

    assert denied.value.status_code == 403
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(models.ImportJob)) == 0
        assert await session.scalar(select(func.count()).select_from(File)) == 0
        denied_events = list(
            await session.scalars(
                select(AuditLog).where(
                    AuditLog.outcome == "DENIED",
                    AuditLog.actor_id == denied_actor.subject_id,
                )
            )
        )
    assert len(denied_events) == 1
    serialized_denial = json.dumps(denied_events[0].metadata_json, ensure_ascii=False)
    raw_manifest = command.model_dump(mode="json")

    def text_values(value: object) -> list[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return [text for item in value for text in text_values(item)]
        if isinstance(value, dict):
            return [text for item in value.values() for text in text_values(item)]
        return []

    assert all(value not in serialized_denial for value in text_values(raw_manifest))
    lowered_denial = serialized_denial.casefold()
    for forbidden_coordinate in (
        "provider secret",
        "authorization",
        "cookie",
        "access_token",
        "refresh_token",
        "object_key",
        "multipart_id",
    ):
        assert forbidden_coordinate not in lowered_denial
    assert storage.create_calls == 0 and storage.expiries == []


@pytest.mark.asyncio
@pytest.mark.parametrize("project_case", ["existing_ungranted", "unknown"])
async def test_create_project_denial_is_uniform_and_uses_only_resolved_audit_fk(
    db_session: AsyncSession,
    active_owner: Any,
    session_factory: async_sessionmaker[AsyncSession],
    project_case: str,
) -> None:
    """A client UUID must not turn mandatory denial evidence into an FK oracle."""
    models, _schemas, _service = import_contract()
    _granted_project, _device, actor = await seed_device_actor(
        db_session,
        active_owner,
        name=f"Create denied target {project_case}",
    )
    if project_case == "existing_ungranted":
        denied_project = Project(name=f"Existing ungranted {uuid4()}")
        db_session.add(denied_project)
        await db_session.commit()
        target_project_id = denied_project.id
        expected_audit_project_id: UUID | None = denied_project.id
    else:
        target_project_id = uuid4()
        expected_audit_project_id = None
    command = command_for(target_project_id)
    storage = InMemoryObjectStorage()
    request_id = uuid4()

    with pytest.raises(DomainError) as denied:
        await service_for(session_factory, storage).create(
            actor,
            command,
            f"project-denied-{project_case}",
            request_id=request_id,
        )

    assert denied.value.status_code == 403
    assert denied.value.code == "IMPORT_CREATE_FORBIDDEN"
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(models.ImportJob)) == 0
        assert await session.scalar(select(func.count()).select_from(models.ImportAttachment)) == 0
        assert await session.scalar(select(func.count()).select_from(File)) == 0
        assert await session.scalar(select(func.count()).select_from(Upload)) == 0
        assert await session.scalar(select(func.count()).select_from(FileUploadLifecycle)) == 0
        audits = list(
            await session.scalars(
                select(AuditLog).where(
                    AuditLog.request_id == request_id,
                    AuditLog.action == "import.create",
                    AuditLog.outcome == "DENIED",
                )
            )
        )
    assert len(audits) == 1
    assert audits[0].project_id == expected_audit_project_id
    serialized_audit = json.dumps(audits[0].metadata_json, ensure_ascii=False)
    assert str(target_project_id) not in serialized_audit
    assert command.local_task_id not in serialized_audit
    assert command.k3_result.suggested_title not in serialized_audit
    assert storage.create_calls == 0 and storage.active == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("part_number", [1, 10_000])
async def test_attachment_part_presign_reuses_task7_bounds_and_fifteen_minute_expiry(
    db_session: AsyncSession,
    active_owner: Any,
    session_factory: async_sessionmaker[AsyncSession],
    part_number: int,
) -> None:
    """The import boundary resolves its own Upload and delegates Task 7 URL semantics."""
    project, device, creator = await seed_device_actor(
        db_session, active_owner, name=f"Import part boundary {part_number}"
    )
    storage = ProbeTrackingStorage()
    service = service_for(session_factory, storage)
    job = await service.create(
        creator,
        command_for(project.id),
        f"part-boundary-{part_number}",
        request_id=uuid4(),
    )
    attachment = job.attachments[0]
    upload_actor = Actor(
        "device", device.id, None, frozenset({project.id}), frozenset({"imports:upload"})
    )
    before_audits = await db_session.scalar(select(func.count()).select_from(AuditLog))

    url = await service.presign_part(
        upload_actor,
        job.id,
        attachment.id,
        part_number,
        request_id=uuid4(),
    )

    assert url.endswith(f"/{part_number}")
    assert storage.expiries == [900] and storage.probe_calls == ["presign"]
    assert await db_session.scalar(select(func.count()).select_from(AuditLog)) == before_audits


@pytest.mark.asyncio
@pytest.mark.parametrize("part_number", [0, 10_001])
async def test_attachment_part_presign_rejects_out_of_range_before_storage(
    db_session: AsyncSession,
    active_owner: Any,
    session_factory: async_sessionmaker[AsyncSession],
    part_number: int,
) -> None:
    """The existing 1..10000 multipart range remains the only accepted range."""
    project, _device, actor = await seed_device_actor(
        db_session, active_owner, name=f"Invalid import part {part_number}"
    )
    storage = ProbeTrackingStorage()
    service = service_for(session_factory, storage)
    job = await service.create(
        actor,
        command_for(project.id),
        f"invalid-part-{part_number}",
        request_id=uuid4(),
    )
    attachment = job.attachments[0]
    checkpoint = list(storage.probe_calls)

    with pytest.raises((ValueError, DomainError)):
        await service.presign_part(
            actor,
            job.id,
            attachment.id,
            part_number,
            request_id=uuid4(),
        )

    assert storage.probe_calls == checkpoint and storage.expiries == []


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["part", "complete"])
@pytest.mark.parametrize(
    "probe_case",
    [
        "browser",
        "device_role",
        "scope",
        "project",
        "second_device",
        "other_job",
        "other_project",
        "unknown",
    ],
)
async def test_attachment_operations_reject_actor_or_id_mixing_before_storage(
    db_session: AsyncSession,
    active_owner: Any,
    session_factory: async_sessionmaker[AsyncSession],
    operation: str,
    probe_case: str,
) -> None:
    """A device cannot splice jobs, attachments, devices, or projects into an upload probe."""
    models, _schemas, _service = import_contract()
    project, device, actor = await seed_device_actor(
        db_session, active_owner, name=f"Attachment probe {operation} {probe_case}"
    )
    storage = ProbeTrackingStorage(complete_size=128)
    service = service_for(session_factory, storage)
    target = await service.create(
        actor,
        command_for(project.id),
        f"probe-target-{operation}-{probe_case}",
        request_id=uuid4(),
    )
    same_project_other = await service.create(
        actor,
        command_for(project.id),
        f"probe-same-project-{operation}-{probe_case}",
        request_id=uuid4(),
    )

    other_project = Project(name=f"Other probe project {operation} {probe_case}")
    db_session.add(other_project)
    await db_session.flush()
    db_session.add(DeviceProjectGrant(device_id=device.id, project_id=other_project.id))
    await db_session.commit()
    two_project_actor = Actor(
        "device",
        device.id,
        None,
        frozenset({project.id, other_project.id}),
        actor.scopes,
    )
    other_project_job = await service.create(
        two_project_actor,
        command_for(other_project.id),
        f"probe-other-project-{operation}-{probe_case}",
        request_id=uuid4(),
    )
    _foreign_project, _foreign_device, foreign_actor = await seed_device_actor(
        db_session, active_owner, name=f"Foreign probe device {operation} {probe_case}"
    )

    request_actor = actor
    job_id = target.id
    attachment_id = target.attachments[0].id
    if probe_case == "browser":
        request_actor = Actor(
            "user", active_owner.id, Role.OWNER, frozenset({project.id}), frozenset()
        )
    elif probe_case == "device_role":
        request_actor = Actor("device", device.id, Role.OWNER, actor.project_ids, actor.scopes)
    elif probe_case == "scope":
        request_actor = Actor(
            "device",
            device.id,
            None,
            actor.project_ids,
            frozenset(actor.scopes - {"imports:upload"}),
        )
    elif probe_case == "project":
        request_actor = Actor("device", device.id, None, frozenset(), actor.scopes)
    elif probe_case == "second_device":
        request_actor = Actor(
            "device",
            foreign_actor.subject_id,
            None,
            frozenset({project.id}),
            actor.scopes,
        )
    elif probe_case == "other_job":
        attachment_id = same_project_other.attachments[0].id
    elif probe_case == "other_project":
        attachment_id = other_project_job.attachments[0].id
    else:
        job_id, attachment_id = uuid4(), uuid4()

    request_id = uuid4()
    checkpoint = list(storage.probe_calls)
    if operation == "part":
        call = service.presign_part(
            request_actor, job_id, attachment_id, 1, request_id=request_id
        )
        expected_action = "import.attachment.part_url"
    else:
        call = service.complete_attachment(
            request_actor,
            job_id,
            attachment_id,
            [CompletedPart(1, "private-etag")],
            request_id=request_id,
        )
        expected_action = "import.attachment.complete"

    with pytest.raises(DomainError) as denied:
        await call

    if probe_case in {"browser", "device_role", "scope", "project"}:
        assert denied.value.status_code == 403
        assert denied.value.code == "IMPORT_UPLOAD_FORBIDDEN"
    else:
        assert denied.value.status_code == 404
        assert denied.value.code == "IMPORT_ATTACHMENT_NOT_FOUND"
    assert storage.probe_calls == checkpoint
    async with session_factory() as session:
        denial = await session.scalar(
            select(AuditLog).where(
                AuditLog.request_id == request_id,
                AuditLog.action == expected_action,
                AuditLog.outcome == "DENIED",
            )
        )
        object_keys = list(await session.scalars(select(File.object_key)))
        assert await session.scalar(select(func.count()).select_from(models.ImportJob)) == 3
    assert denial is not None
    assert set(denial.metadata_json) <= {
        "actor_role",
        "reason",
        "error_code",
        "job_id",
        "attachment_id",
    }
    serialized = json.dumps(denial.metadata_json, ensure_ascii=False).casefold()
    for unsafe in (
        "private-etag",
        "authorization",
        "cookie",
        "access_token",
        "refresh_token",
        "object_key",
        "multipart_id",
        *object_keys,
        *storage.active,
    ):
        assert unsafe.casefold() not in serialized


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["part", "complete"])
async def test_attachment_denial_audit_failure_propagates_before_storage(
    db_session: AsyncSession,
    active_owner: Any,
    session_factory: async_sessionmaker[AsyncSession],
    operation: str,
) -> None:
    """Authenticated denials are mandatory evidence and precede every provider operation."""
    project, device, creator = await seed_device_actor(
        db_session, active_owner, name=f"Attachment denial audit {operation}"
    )
    storage = ProbeTrackingStorage(complete_size=128)
    service = service_for(session_factory, storage)
    job = await service.create(
        creator,
        command_for(project.id),
        f"denial-audit-{operation}",
        request_id=uuid4(),
    )
    attachment = job.attachments[0]
    denied_actor = Actor(
        "device", device.id, None, frozenset({project.id}), frozenset({"imports:create"})
    )
    expected_action = (
        "import.attachment.part_url"
        if operation == "part"
        else "import.attachment.complete"
    )

    def fail_denial(_mapper: object, _connection: object, target: AuditLog) -> None:
        if target.action == expected_action and target.outcome == "DENIED":
            raise RuntimeError("audit unavailable")

    checkpoint = list(storage.probe_calls)
    event.listen(AuditLog, "before_insert", fail_denial)
    try:
        with pytest.raises(RuntimeError, match="audit unavailable"):
            if operation == "part":
                await service.presign_part(
                    denied_actor, job.id, attachment.id, 1, request_id=uuid4()
                )
            else:
                await service.complete_attachment(
                    denied_actor,
                    job.id,
                    attachment.id,
                    [CompletedPart(1, "private-etag")],
                    request_id=uuid4(),
                )
    finally:
        event.remove(AuditLog, "before_insert", fail_denial)

    assert storage.probe_calls == checkpoint


@pytest.mark.asyncio
async def test_attachment_completion_reuses_quarantine_outbox_audit_and_replay(
    db_session: AsyncSession,
    active_owner: Any,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """One Task 7 completion yields stable quarantine, outbox, audit, and scan evidence."""
    project, device, creator = await seed_device_actor(
        db_session, active_owner, name="Import attachment completion"
    )
    storage = ProbeTrackingStorage(complete_size=128)
    service = service_for(session_factory, storage)
    job = await service.create(
        creator,
        command_for(project.id),
        "attachment-completion",
        request_id=uuid4(),
    )
    attachment = job.attachments[0]
    upload_actor = Actor(
        "device", device.id, None, frozenset({project.id}), frozenset({"imports:upload"})
    )
    request_id = uuid4()
    parts = [CompletedPart(2, "private-etag-2"), CompletedPart(1, "private-etag-1")]

    first = await service.complete_attachment(
        upload_actor, job.id, attachment.id, parts, request_id=request_id
    )
    storage_checkpoint = list(storage.probe_calls)
    second = await service.complete_attachment(
        upload_actor,
        job.id,
        attachment.id,
        list(reversed(parts)),
        request_id=request_id,
    )

    assert first.id == second.id == attachment.file_id
    assert first.state == second.state == FileState.QUARANTINED
    assert storage.complete_calls == 1 and storage.probe_calls == storage_checkpoint
    async with session_factory() as session:
        lifecycle = await session.get(FileUploadLifecycle, attachment.upload_id)
        outboxes = list(
            await session.scalars(
                select(FileLifecycleOutbox)
                .where(FileLifecycleOutbox.file_id == attachment.file_id)
                .order_by(FileLifecycleOutbox.kind)
            )
        )
    assert lifecycle is not None
    assert lifecycle.multipart_id is not None
    assert lifecycle.canonical_parts_json == [
        {"part_number": 1, "etag": "private-etag-1"},
        {"part_number": 2, "etag": "private-etag-2"},
    ]
    assert [(row.kind, row.state) for row in outboxes] == [
        ("completion_audit", "PENDING"),
        ("scan_dispatch", "PENDING"),
    ]

    def fail_success_audit(_mapper: object, _connection: object, target: AuditLog) -> None:
        if target.action == "file.upload.complete" and target.outcome == "SUCCESS":
            raise RuntimeError("audit unavailable")

    dispatched: list[tuple[UUID, UUID]] = []
    lifecycle_service = FileLifecycleService(
        session_factory,
        storage,
        lambda file_id, event_key: dispatched.append((file_id, event_key)),
    )
    event.listen(AuditLog, "before_insert", fail_success_audit)
    try:
        assert not await lifecycle_service.deliver_completion(attachment.upload_id)
    finally:
        event.remove(AuditLog, "before_insert", fail_success_audit)

    assert storage.probe_calls == storage_checkpoint and dispatched == []
    async with session_factory() as session, session.begin():
        file = await session.get(File, attachment.file_id)
        completion_outbox = await session.scalar(
            select(FileLifecycleOutbox).where(
                FileLifecycleOutbox.file_id == attachment.file_id,
                FileLifecycleOutbox.kind == "completion_audit",
            )
        )
        assert file is not None and file.state == FileState.QUARANTINED
        assert completion_outbox is not None
        assert completion_outbox.state == "PENDING"
        assert completion_outbox.last_error_code == "AUDIT_FAILED"
        completion_outbox.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)

    assert await lifecycle_service.deliver_completion(attachment.upload_id)
    assert await lifecycle_service.deliver_completion(attachment.upload_id)
    assert storage.probe_calls == storage_checkpoint
    assert len(dispatched) == 1 and dispatched[0][0] == attachment.file_id
    async with session_factory() as session:
        audits = list(
            await session.scalars(
                select(AuditLog).where(
                    AuditLog.action == "file.upload.complete",
                    AuditLog.object_id == attachment.file_id,
                )
            )
        )
        delivered_outboxes = list(
            await session.scalars(
                select(FileLifecycleOutbox).where(
                    FileLifecycleOutbox.file_id == attachment.file_id
                )
            )
        )
    assert len(audits) == 1
    assert audits[0].actor_kind == "device" and audits[0].actor_id == device.id
    assert audits[0].metadata_json == {
        "actor_role": None,
        "state": "QUARANTINED",
        "size_bytes": 128,
    }
    assert {row.state for row in delivered_outboxes} == {"DELIVERED"}
    serialized_audit = json.dumps(audits[0].metadata_json, ensure_ascii=False)
    assert "private-etag" not in serialized_audit
    assert lifecycle.object_key not in serialized_audit
    assert lifecycle.multipart_id not in serialized_audit


async def set_attachment_states(
    session_factory: async_sessionmaker[AsyncSession],
    models: ModuleType,
    job_id: UUID,
    states_by_kind: dict[str, str],
    *,
    scan_results: dict[str, str] | None = None,
) -> dict[str, UUID]:
    file_ids: dict[str, UUID] = {}
    async with session_factory() as session, session.begin():
        attachments = list(
            await session.scalars(
                select(models.ImportAttachment).where(
                    models.ImportAttachment.job_id == job_id
                )
            )
        )
        for attachment in attachments:
            kind = attachment.kind.value
            file = await session.get(File, attachment.file_id)
            lifecycle = await session.get(FileUploadLifecycle, attachment.upload_id)
            assert file is not None and lifecycle is not None
            file_ids[kind] = file.id
            file.state = states_by_kind[kind]
            if states_by_kind[kind] != "UPLOADING":
                lifecycle.completion_state = "QUARANTINED"
            if scan_results is not None and kind in scan_results:
                file.scan_result = scan_results[kind]
    return file_ids


@pytest.mark.asyncio
async def test_submit_refuses_any_attachment_still_uploading_without_state_change(
    db_session: AsyncSession,
    active_owner: Any,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Submission must not disguise an incomplete multipart as scanning work."""
    models, _schemas, _service = import_contract()
    project, _device, actor = await seed_device_actor(
        db_session, active_owner, name="Incomplete submit"
    )
    storage = InMemoryObjectStorage()
    service = service_for(session_factory, storage)
    job = await service.create(
        actor, command_for(project.id), "incomplete-submit", request_id=uuid4()
    )
    provider_counts = (storage.create_calls, storage.complete_calls, len(storage.expiries))
    request_id = uuid4()

    with pytest.raises(DomainError) as incomplete:
        await service.submit(actor, job.id, request_id=request_id)

    assert incomplete.value.status_code == 409
    assert incomplete.value.code == "IMPORT_ATTACHMENTS_INCOMPLETE"
    async with session_factory() as session:
        saved = await session.get(models.ImportJob, job.id)
        denials = list(
            await session.scalars(
                select(AuditLog).where(
                    AuditLog.action == "import.submit",
                    AuditLog.object_id == job.id,
                    AuditLog.outcome == "DENIED",
                    AuditLog.request_id == request_id,
                )
            )
        )
    assert saved is not None and saved.status == models.ImportStatus.UPLOADING
    assert saved.submitted_at is None and len(denials) == 1
    assert set(denials[0].metadata_json) <= {"actor_role", "error_code", "job_id"}
    assert (storage.create_calls, storage.complete_calls, len(storage.expiries)) == provider_counts


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "actor_case",
    [
        "valid_exact_scope",
        "browser",
        "device_role",
        "scope",
        "project",
        "second_device",
        "unknown_job",
    ],
)
async def test_submit_requires_exact_scope_current_project_and_job_owner(
    db_session: AsyncSession,
    active_owner: Any,
    session_factory: async_sessionmaker[AsyncSession],
    actor_case: str,
) -> None:
    """Submit is a device-only owned-job boundary with one safe denial event."""
    models, _schemas, _service = import_contract()
    project, device, creator = await seed_device_actor(
        db_session, active_owner, name=f"Submit authorization {actor_case}"
    )
    storage = InMemoryObjectStorage()
    service = service_for(session_factory, storage)
    job = await service.create(
        creator,
        command_for(project.id),
        f"submit-authorization-{actor_case}",
        request_id=uuid4(),
    )
    await set_attachment_states(
        session_factory,
        models,
        job.id,
        {"K3_RAW": "CLEAN"},
        scan_results={"K3_RAW": "CLEAN"},
    )
    actor = Actor(
        "device",
        device.id,
        None,
        frozenset({project.id}),
        frozenset({"imports:submit"}),
    )
    job_id = job.id
    if actor_case == "browser":
        actor = Actor(
            "user", active_owner.id, Role.OWNER, frozenset({project.id}), frozenset()
        )
    elif actor_case == "device_role":
        actor = Actor("device", device.id, Role.OWNER, actor.project_ids, actor.scopes)
    elif actor_case == "scope":
        actor = Actor(
            "device", device.id, None, actor.project_ids, frozenset({"imports:upload"})
        )
    elif actor_case == "project":
        actor = Actor("device", device.id, None, frozenset(), actor.scopes)
    elif actor_case == "second_device":
        _project, _device, foreign_actor = await seed_device_actor(
            db_session, active_owner, name="Foreign submit device"
        )
        actor = Actor(
            "device",
            foreign_actor.subject_id,
            None,
            frozenset({project.id}),
            frozenset({"imports:submit"}),
        )
    elif actor_case == "unknown_job":
        job_id = uuid4()

    request_id = uuid4()
    provider_counts = (storage.create_calls, storage.complete_calls, len(storage.expiries))
    if actor_case == "valid_exact_scope":
        submitted = await service.submit(actor, job_id, request_id=request_id)
        assert submitted.status == models.ImportStatus.RECEIVED
        async with session_factory() as session:
            success = list(
                await session.scalars(
                    select(AuditLog).where(
                        AuditLog.action == "import.submit",
                        AuditLog.object_id == job.id,
                        AuditLog.outcome == "SUCCESS",
                    )
                )
            )
        assert len(success) == 1
    else:
        with pytest.raises(DomainError) as denied:
            await service.submit(actor, job_id, request_id=request_id)
        if actor_case in {"second_device", "unknown_job"}:
            assert denied.value.status_code == 404
            assert denied.value.code == "IMPORT_JOB_NOT_FOUND"
        else:
            assert denied.value.status_code == 403
            assert denied.value.code == "IMPORT_SUBMIT_FORBIDDEN"
        async with session_factory() as session:
            denial = await session.scalar(
                select(AuditLog).where(
                    AuditLog.action == "import.submit",
                    AuditLog.request_id == request_id,
                    AuditLog.outcome == "DENIED",
                )
            )
            saved = await session.get(models.ImportJob, job.id)
            object_keys = list(await session.scalars(select(File.object_key)))
        assert denial is not None
        assert saved is not None and saved.status == models.ImportStatus.UPLOADING
        assert set(denial.metadata_json) <= {"actor_role", "error_code", "job_id"}
        serialized = json.dumps(denial.metadata_json, ensure_ascii=False).casefold()
        for unsafe in (
            "authorization",
            "cookie",
            "access_token",
            "refresh_token",
            "object_key",
            "multipart_id",
            *object_keys,
            *storage.active,
        ):
            assert unsafe.casefold() not in serialized
    assert (storage.create_calls, storage.complete_calls, len(storage.expiries)) == provider_counts


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("file_state", "expected_status"),
    [("QUARANTINED", "SCANNING"), ("CLEAN", "RECEIVED")],
)
async def test_submit_success_audit_failure_rolls_back_transition_then_recovers_once(
    db_session: AsyncSession,
    active_owner: Any,
    session_factory: async_sessionmaker[AsyncSession],
    file_state: str,
    expected_status: str,
) -> None:
    """Neither a waiting nor terminal submit state may commit without its success evidence."""
    models, _schemas, _service = import_contract()
    project, device, creator = await seed_device_actor(
        db_session, active_owner, name=f"Submit success audit {expected_status}"
    )
    storage = InMemoryObjectStorage()
    service = service_for(session_factory, storage)
    job = await service.create(
        creator,
        command_for(project.id),
        f"submit-success-audit-{expected_status}",
        request_id=uuid4(),
    )
    file_ids = await set_attachment_states(
        session_factory,
        models,
        job.id,
        {"K3_RAW": file_state},
        scan_results={"K3_RAW": "CLEAN"} if file_state == "CLEAN" else None,
    )
    submitter = Actor(
        "device",
        device.id,
        None,
        frozenset({project.id}),
        frozenset({"imports:submit"}),
    )
    provider_counts = (storage.create_calls, storage.complete_calls, len(storage.expiries))

    def fail_submit_success(_mapper: object, _connection: object, target: AuditLog) -> None:
        if (
            target.action == "import.submit"
            and target.object_id == job.id
            and target.outcome == "SUCCESS"
        ):
            raise RuntimeError("audit unavailable")

    event.listen(AuditLog, "before_insert", fail_submit_success)
    try:
        with pytest.raises(RuntimeError, match="audit unavailable"):
            await service.submit(submitter, job.id, request_id=uuid4())
    finally:
        event.remove(AuditLog, "before_insert", fail_submit_success)

    async with session_factory() as session:
        rolled_back = await session.get(models.ImportJob, job.id)
        file = await session.get(File, file_ids["K3_RAW"])
        success_before_retry = list(
            await session.scalars(
                select(AuditLog).where(
                    AuditLog.action == "import.submit",
                    AuditLog.object_id == job.id,
                    AuditLog.outcome == "SUCCESS",
                )
            )
        )
    assert rolled_back is not None
    assert rolled_back.status == models.ImportStatus.UPLOADING
    assert rolled_back.submitted_at is None
    assert file is not None and file.state.value == file_state
    assert success_before_retry == []
    assert (storage.create_calls, storage.complete_calls, len(storage.expiries)) == provider_counts

    recovered = await service.submit(submitter, job.id, request_id=uuid4())

    async with session_factory() as session:
        saved = await session.get(models.ImportJob, job.id)
        success_after_retry = list(
            await session.scalars(
                select(AuditLog).where(
                    AuditLog.action == "import.submit",
                    AuditLog.object_id == job.id,
                    AuditLog.outcome == "SUCCESS",
                )
            )
        )
    assert recovered.status == getattr(models.ImportStatus, expected_status)
    assert saved is not None and saved.status == getattr(models.ImportStatus, expected_status)
    assert saved.submitted_at is not None
    assert len(success_after_retry) == 1
    assert success_after_retry[0].metadata_json.get("status") == expected_status
    assert (storage.create_calls, storage.complete_calls, len(storage.expiries)) == provider_counts


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("denial_case", "expected_status", "expected_code"),
    [
        ("scope", 403, "IMPORT_SUBMIT_FORBIDDEN"),
        ("incomplete", 409, "IMPORT_ATTACHMENTS_INCOMPLETE"),
    ],
)
async def test_submit_denial_audit_failure_preserves_state_then_records_once(
    db_session: AsyncSession,
    active_owner: Any,
    session_factory: async_sessionmaker[AsyncSession],
    denial_case: str,
    expected_status: int,
    expected_code: str,
) -> None:
    """Mandatory denial evidence fails before mutation or I/O and leaves a clean retry."""
    models, _schemas, _service = import_contract()
    project, device, creator = await seed_device_actor(
        db_session, active_owner, name=f"Submit denial audit {denial_case}"
    )
    storage = InMemoryObjectStorage()
    service = service_for(session_factory, storage)
    job = await service.create(
        creator,
        command_for(project.id),
        f"submit-denial-audit-{denial_case}",
        request_id=uuid4(),
    )
    actor = Actor(
        "device",
        device.id,
        None,
        frozenset({project.id}),
        frozenset({"imports:submit"}),
    )
    if denial_case == "scope":
        actor = Actor(
            "device", device.id, None, actor.project_ids, frozenset({"imports:create"})
        )
    request_id = uuid4()
    provider_counts = (storage.create_calls, storage.complete_calls, len(storage.expiries))

    def fail_submit_denial(_mapper: object, _connection: object, target: AuditLog) -> None:
        if (
            target.action == "import.submit"
            and target.object_id == job.id
            and target.outcome == "DENIED"
        ):
            raise RuntimeError("audit unavailable")

    event.listen(AuditLog, "before_insert", fail_submit_denial)
    try:
        with pytest.raises(RuntimeError, match="audit unavailable"):
            await service.submit(actor, job.id, request_id=request_id)
    finally:
        event.remove(AuditLog, "before_insert", fail_submit_denial)

    async with session_factory() as session:
        saved = await session.get(models.ImportJob, job.id)
        files = list(
            await session.scalars(
                select(File)
                .join(
                    models.ImportAttachment,
                    models.ImportAttachment.file_id == File.id,
                )
                .where(models.ImportAttachment.job_id == job.id)
            )
        )
        failed_audits = list(
            await session.scalars(
                select(AuditLog).where(
                    AuditLog.action == "import.submit",
                    AuditLog.object_id == job.id,
                    AuditLog.request_id == request_id,
                )
            )
        )
    assert saved is not None and saved.status == models.ImportStatus.UPLOADING
    assert saved.submitted_at is None
    assert files and {file.state for file in files} == {FileState.UPLOADING}
    assert failed_audits == []
    assert (storage.create_calls, storage.complete_calls, len(storage.expiries)) == provider_counts

    with pytest.raises(DomainError) as denied:
        await service.submit(actor, job.id, request_id=request_id)

    assert denied.value.status_code == expected_status
    assert denied.value.code == expected_code
    async with session_factory() as session:
        saved = await session.get(models.ImportJob, job.id)
        denials = list(
            await session.scalars(
                select(AuditLog).where(
                    AuditLog.action == "import.submit",
                    AuditLog.object_id == job.id,
                    AuditLog.request_id == request_id,
                    AuditLog.outcome == "DENIED",
                )
            )
        )
    assert saved is not None and saved.status == models.ImportStatus.UPLOADING
    assert saved.submitted_at is None and len(denials) == 1
    assert denials[0].metadata_json.get("error_code") == expected_code
    assert (storage.create_calls, storage.complete_calls, len(storage.expiries)) == provider_counts


@pytest.mark.asyncio
@pytest.mark.parametrize("file_state", ["QUARANTINED", "SCANNING"])
async def test_submit_commits_scanning_while_any_completed_attachment_is_not_terminal(
    db_session: AsyncSession,
    active_owner: Any,
    session_factory: async_sessionmaker[AsyncSession],
    file_state: str,
) -> None:
    """A completed multipart awaiting a scan must yield SCANNING, never RECEIVED."""
    models, _schemas, _service = import_contract()
    project, _device, actor = await seed_device_actor(
        db_session, active_owner, name=f"Scanning submit {file_state}"
    )
    storage = InMemoryObjectStorage()
    service = service_for(session_factory, storage)
    job = await service.create(
        actor, command_for(project.id), f"scanning-{file_state}", request_id=uuid4()
    )
    await set_attachment_states(
        session_factory, models, job.id, {"K3_RAW": file_state}
    )

    request_id = uuid4()
    submitted = await service.submit(actor, job.id, request_id=request_id)

    assert submitted.status == models.ImportStatus.SCANNING
    assert submitted.submitted_at is not None
    assert submitted.submitted_at.tzinfo is not None
    async with session_factory() as session:
        audit = await session.scalar(
            select(AuditLog).where(
                AuditLog.action == "import.submit",
                AuditLog.object_id == job.id,
                AuditLog.request_id == request_id,
                AuditLog.outcome == "SUCCESS",
            )
        )
    assert audit is not None and audit.metadata_json.get("status") == "SCANNING"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("actual_original_sha256", "expected_status"),
    [("a" * 64, "RECEIVED"), ("c" * 64, "CONFLICT")],
)
async def test_all_clean_files_receive_unless_verified_original_conflicts_with_base(
    db_session: AsyncSession,
    active_owner: Any,
    session_factory: async_sessionmaker[AsyncSession],
    actual_original_sha256: str,
    expected_status: str,
) -> None:
    """A clean but stale original must pause as CONFLICT rather than silently win."""
    models, schemas, _service = import_contract()
    project, _device, actor = await seed_device_actor(
        db_session, active_owner, name=f"Clean submit {expected_status}"
    )
    payload = manifest_payload(project.id, two_attachments=True)
    payload["base_sha256"] = "a" * 64
    command = schemas.ImportJobCreate.model_validate(payload)
    service = service_for(session_factory, InMemoryObjectStorage())
    job = await service.create(
        actor, command, f"clean-{expected_status}", request_id=uuid4()
    )
    await set_attachment_states(
        session_factory,
        models,
        job.id,
        {"ORIGINAL": "CLEAN", "K3_RAW": "CLEAN"},
        scan_results={"ORIGINAL": "CLEAN", "K3_RAW": "CLEAN"},
    )
    async with session_factory() as session, session.begin():
        original = await session.scalar(
            select(models.ImportAttachment).where(
                models.ImportAttachment.job_id == job.id,
                models.ImportAttachment.kind == models.AttachmentKind.ORIGINAL,
            )
        )
        assert original is not None
        original_file = await session.get(File, original.file_id)
        assert original_file is not None
        original_file.sha256 = actual_original_sha256

    submitted = await service.submit(actor, job.id, request_id=uuid4())

    assert submitted.status == getattr(models.ImportStatus, expected_status)
    if expected_status == "CONFLICT":
        assert submitted.result_code == "BASE_SHA256_MISMATCH"
    else:
        assert submitted.result_code is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("file_state", "scan_result", "expected_result_code"),
    [
        ("INFECTED", "Eicar-Test-Signature", "ATTACHMENT_INFECTED"),
        ("FAILED", "provider secret", "ATTACHMENT_SCAN_FAILED"),
    ],
)
async def test_infected_or_terminal_failed_attachment_rejects_with_safe_server_code(
    db_session: AsyncSession,
    active_owner: Any,
    session_factory: async_sessionmaker[AsyncSession],
    file_state: str,
    scan_result: str,
    expected_result_code: str,
) -> None:
    """Raw scanner/provider text must not become a public import result code."""
    models, _schemas, _service = import_contract()
    project, _device, actor = await seed_device_actor(
        db_session, active_owner, name=f"Rejected submit {file_state}"
    )
    service = service_for(session_factory, InMemoryObjectStorage())
    job = await service.create(
        actor, command_for(project.id), f"reject-{file_state}", request_id=uuid4()
    )
    await set_attachment_states(
        session_factory,
        models,
        job.id,
        {"K3_RAW": file_state},
        scan_results={"K3_RAW": scan_result},
    )

    submitted = await service.submit(actor, job.id, request_id=uuid4())

    assert submitted.status == models.ImportStatus.REJECTED
    assert submitted.result_code == expected_result_code
    assert scan_result.lower() not in submitted.result_code.lower()


@pytest.mark.asyncio
async def test_terminal_submit_replay_is_monotonic_and_has_no_io_or_duplicate_audit(
    db_session: AsyncSession,
    active_owner: Any,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A late contradictory file observation must not reopen a terminal import."""
    models, _schemas, _service = import_contract()
    project, _device, actor = await seed_device_actor(
        db_session, active_owner, name="Terminal replay"
    )
    storage = InMemoryObjectStorage()
    service = service_for(session_factory, storage)
    job = await service.create(
        actor, command_for(project.id), "terminal-replay", request_id=uuid4()
    )
    file_ids = await set_attachment_states(
        session_factory,
        models,
        job.id,
        {"K3_RAW": "CLEAN"},
        scan_results={"K3_RAW": "CLEAN"},
    )
    terminal = await service.submit(actor, job.id, request_id=uuid4())
    async with session_factory() as session:
        audits_before = list(
            await session.scalars(
                select(AuditLog).where(AuditLog.object_id == job.id)
            )
        )
    provider_counts = (storage.create_calls, storage.complete_calls, len(storage.expiries))

    async with session_factory() as session, session.begin():
        file = await session.get(File, file_ids["K3_RAW"])
        assert file is not None
        file.state = "INFECTED"
        file.scan_result = "late scanner secret"
    replay = await service.submit(actor, job.id, request_id=uuid4())
    await service.reconcile_file(file_ids["K3_RAW"])

    async with session_factory() as session:
        saved = await session.get(models.ImportJob, job.id)
        audits_after = list(
            await session.scalars(
                select(AuditLog).where(AuditLog.object_id == job.id)
            )
        )
    assert terminal.status == replay.status == models.ImportStatus.RECEIVED
    assert saved is not None and saved.status == models.ImportStatus.RECEIVED
    assert len(audits_after) == len(audits_before)
    assert (storage.create_calls, storage.complete_calls, len(storage.expiries)) == provider_counts


@pytest.mark.asyncio
async def test_terminal_reconcile_audit_failure_rolls_back_only_the_import_transition(
    db_session: AsyncSession,
    active_owner: Any,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A committed clean File may not expose an unaudited terminal import state."""
    models, _schemas, _service = import_contract()
    project, _device, actor = await seed_device_actor(
        db_session, active_owner, name="Terminal audit rollback"
    )
    service = service_for(session_factory, InMemoryObjectStorage())
    job = await service.create(
        actor, command_for(project.id), "terminal-audit", request_id=uuid4()
    )
    file_ids = await set_attachment_states(
        session_factory, models, job.id, {"K3_RAW": "QUARANTINED"}
    )
    scanning = await service.submit(actor, job.id, request_id=uuid4())
    assert scanning.status == models.ImportStatus.SCANNING
    await set_attachment_states(
        session_factory,
        models,
        job.id,
        {"K3_RAW": "CLEAN"},
        scan_results={"K3_RAW": "CLEAN"},
    )

    def fail_terminal(_mapper: object, _connection: object, target: AuditLog) -> None:
        if (
            target.object_type == "import_job"
            and target.object_id == job.id
            and target.metadata_json.get("status") == "RECEIVED"
        ):
            raise RuntimeError("audit unavailable")

    event.listen(AuditLog, "before_insert", fail_terminal)
    try:
        with pytest.raises(RuntimeError, match="audit unavailable"):
            await service.reconcile_file(file_ids["K3_RAW"])
    finally:
        event.remove(AuditLog, "before_insert", fail_terminal)

    async with session_factory() as session:
        saved = await session.get(models.ImportJob, job.id)
        file = await session.get(File, file_ids["K3_RAW"])
    assert saved is not None and saved.status == models.ImportStatus.SCANNING
    assert file is not None and file.state == "CLEAN"


@pytest.mark.asyncio
async def test_concurrent_submit_and_reconcile_choose_one_terminal_transition(
    db_session: AsyncSession,
    active_owner: Any,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Submit and scan callback races must be bounded, monotonic, and exactly-once audited."""
    models, _schemas, _service = import_contract()
    project, _device, actor = await seed_device_actor(
        db_session, active_owner, name="Submit reconcile race"
    )
    storage = InMemoryObjectStorage()
    setup_service = service_for(session_factory, storage)
    job = await setup_service.create(
        actor, command_for(project.id), "submit-reconcile-race", request_id=uuid4()
    )
    file_ids = await set_attachment_states(
        session_factory,
        models,
        job.id,
        {"K3_RAW": "CLEAN"},
        scan_results={"K3_RAW": "CLEAN"},
    )
    barrier = asyncio.Barrier(2)

    async def submit() -> None:
        await barrier.wait()
        await service_for(session_factory, storage).submit(
            actor, job.id, request_id=uuid4()
        )

    async def reconcile() -> None:
        await barrier.wait()
        await service_for(session_factory, storage).reconcile_file(
            file_ids["K3_RAW"]
        )

    await asyncio.wait_for(asyncio.gather(submit(), reconcile()), timeout=10)

    async with session_factory() as session:
        saved = await session.get(models.ImportJob, job.id)
        audits = list(
            await session.scalars(
                select(AuditLog).where(AuditLog.object_id == job.id)
            )
        )
    terminal_audits = [
        row for row in audits if row.metadata_json.get("status") == "RECEIVED"
    ]
    assert saved is not None and saved.status == models.ImportStatus.RECEIVED
    assert len(terminal_audits) == 1


@pytest.mark.asyncio
async def test_reconcile_file_is_database_only_and_touches_only_referencing_jobs(
    db_session: AsyncSession,
    active_owner: Any,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A scan callback may not sweep unrelated scanning imports or touch object storage."""
    models, _schemas, _service = import_contract()
    project, _device, actor = await seed_device_actor(
        db_session, active_owner, name="Referenced reconcile only"
    )
    storage = InMemoryObjectStorage()
    service = service_for(session_factory, storage)
    target = await service.create(
        actor, command_for(project.id), "referenced-reconcile", request_id=uuid4()
    )
    unrelated = await service.create(
        actor, command_for(project.id), "unrelated-reconcile", request_id=uuid4()
    )
    target_files = await set_attachment_states(
        session_factory,
        models,
        target.id,
        {"K3_RAW": "CLEAN"},
        scan_results={"K3_RAW": "CLEAN"},
    )
    await set_attachment_states(
        session_factory,
        models,
        unrelated.id,
        {"K3_RAW": "CLEAN"},
        scan_results={"K3_RAW": "CLEAN"},
    )
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        for job_id in (target.id, unrelated.id):
            saved = await session.get(models.ImportJob, job_id)
            assert saved is not None
            saved.status = models.ImportStatus.SCANNING
            saved.submitted_at = now
            saved.updated_at = now
    provider_counts = (storage.create_calls, storage.complete_calls, len(storage.expiries))

    await service.reconcile_file(target_files["K3_RAW"])

    async with session_factory() as session:
        saved_target = await session.get(models.ImportJob, target.id)
        saved_unrelated = await session.get(models.ImportJob, unrelated.id)
        terminal_audits = list(
            await session.scalars(
                select(AuditLog).where(
                    AuditLog.action == "import.reconcile",
                    AuditLog.outcome == "SUCCESS",
                )
            )
        )
    assert saved_target is not None and saved_target.status == models.ImportStatus.RECEIVED
    assert saved_unrelated is not None
    assert saved_unrelated.status == models.ImportStatus.SCANNING
    assert len(terminal_audits) == 1 and terminal_audits[0].object_id == target.id
    assert (storage.create_calls, storage.complete_calls, len(storage.expiries)) == provider_counts


@pytest.mark.asyncio
async def test_two_file_reconciles_share_job_lock_and_emit_one_terminal_evidence(
    db_session: AsyncSession,
    active_owner: Any,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Different scan callbacks for one job serialize without deadlock or duplicate evidence."""
    models, _schemas, _service = import_contract()
    project, _device, actor = await seed_device_actor(
        db_session, active_owner, name="Two reconcile race"
    )
    storage = InMemoryObjectStorage()
    job = await service_for(session_factory, storage).create(
        actor,
        command_for(project.id, two_attachments=True),
        "two-reconcile-race",
        request_id=uuid4(),
    )
    file_ids = await set_attachment_states(
        session_factory,
        models,
        job.id,
        {"ORIGINAL": "CLEAN", "K3_RAW": "CLEAN"},
        scan_results={"ORIGINAL": "CLEAN", "K3_RAW": "CLEAN"},
    )
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        saved = await session.get(models.ImportJob, job.id)
        assert saved is not None
        saved.status = models.ImportStatus.SCANNING
        saved.submitted_at = now
        saved.updated_at = now
    barrier = asyncio.Barrier(2)

    async def reconcile(file_id: UUID) -> None:
        await barrier.wait()
        await service_for(session_factory, storage).reconcile_file(file_id)

    await asyncio.wait_for(
        asyncio.gather(
            reconcile(file_ids["ORIGINAL"]),
            reconcile(file_ids["K3_RAW"]),
        ),
        timeout=10,
    )

    async with session_factory() as session:
        saved = await session.get(models.ImportJob, job.id)
        terminal_audits = list(
            await session.scalars(
                select(AuditLog).where(
                    AuditLog.action == "import.reconcile",
                    AuditLog.object_id == job.id,
                    AuditLog.outcome == "SUCCESS",
                )
            )
        )
    assert saved is not None and saved.status == models.ImportStatus.RECEIVED
    assert len(terminal_audits) == 1
