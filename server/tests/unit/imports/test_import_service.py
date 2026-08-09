"""Creation, idempotency, and resumable provisioning contracts for K3 imports."""

import asyncio
import importlib
import json
from datetime import UTC, datetime
from types import ModuleType
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from superboss.core.actors import Actor
from superboss.core.errors import DomainError
from superboss.modules.audit.models import AuditLog
from superboss.modules.devices.models import (
    DeviceConnection,
    DeviceProjectGrant,
    DeviceScopeGrant,
)
from superboss.modules.files.models import File, FileUploadLifecycle, Upload
from superboss.modules.projects.models import Project
from superboss.modules.users.models import Role
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

    with pytest.raises(DomainError) as incomplete:
        await service.submit(actor, job.id, request_id=uuid4())

    assert incomplete.value.status_code == 409
    async with session_factory() as session:
        saved = await session.get(models.ImportJob, job.id)
        denials = list(
            await session.scalars(
                select(AuditLog).where(
                    AuditLog.object_id == job.id,
                    AuditLog.outcome == "DENIED",
                )
            )
        )
    assert saved is not None and saved.status == models.ImportStatus.UPLOADING
    assert saved.submitted_at is None and len(denials) == 1
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

    submitted = await service.submit(actor, job.id, request_id=uuid4())

    assert submitted.status == models.ImportStatus.SCANNING
    assert submitted.submitted_at is not None
    assert submitted.submitted_at.tzinfo is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("base_sha256", "expected_status"),
    [("a" * 64, "RECEIVED"), ("c" * 64, "CONFLICT")],
)
async def test_all_clean_files_receive_unless_verified_original_conflicts_with_base(
    db_session: AsyncSession,
    active_owner: Any,
    session_factory: async_sessionmaker[AsyncSession],
    base_sha256: str,
    expected_status: str,
) -> None:
    """A clean but stale original must pause as CONFLICT rather than silently win."""
    models, schemas, _service = import_contract()
    project, _device, actor = await seed_device_actor(
        db_session, active_owner, name=f"Clean submit {expected_status}"
    )
    payload = manifest_payload(project.id, two_attachments=True)
    payload["base_sha256"] = base_sha256
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

    submitted = await service.submit(actor, job.id, request_id=uuid4())

    assert submitted.status == getattr(models.ImportStatus, expected_status)
    if expected_status == "CONFLICT":
        assert submitted.result_code is not None
    else:
        assert submitted.result_code is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("file_state", "scan_result"),
    [("INFECTED", "Eicar-Test-Signature"), ("FAILED", "provider secret")],
)
async def test_infected_or_terminal_failed_attachment_rejects_with_safe_server_code(
    db_session: AsyncSession,
    active_owner: Any,
    session_factory: async_sessionmaker[AsyncSession],
    file_state: str,
    scan_result: str,
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
    assert submitted.result_code is not None and len(submitted.result_code) <= 64
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
