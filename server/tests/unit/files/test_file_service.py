"""File upload state-machine behavior."""

from datetime import date
from uuid import UUID, uuid4

import pytest

from superboss.core.actors import Actor
from superboss.modules.projects.models import Project, ProjectMember
from superboss.modules.users.models import Role, User, UserStatus
from tests.files.storage import InMemoryObjectStorage


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("../secret.pdf", "secret.pdf"), ("..\\secret.pdf", "secret.pdf"), ("\x00", "file")],
)
def test_object_key_segment_never_preserves_path_or_control(raw: str, expected: str) -> None:
    """A raw filename must never become a key path component."""
    from superboss.modules.files.service import FileService

    assert FileService._segment(raw, "file") == expected


@pytest.mark.asyncio
async def test_memory_storage_completes_and_presigns_behaviorally() -> None:
    """The fake must retain completed parts and expiry as its external state."""
    from superboss.modules.files.storage import CompletedPart

    storage = InMemoryObjectStorage(complete_size=7)
    upload = await storage.create_multipart("projects/x/a", "application/pdf")
    assert (
        await storage.presign_upload_part("projects/x/a", upload, 1, 300)
        == f"memory://part/{upload}/1"
    )
    metadata = await storage.complete_multipart("projects/x/a", upload, [CompletedPart(1, "e")])
    assert metadata.size_bytes == 7 and storage.completed[upload] == [CompletedPart(1, "e")]
    assert await storage.presign_get("projects/x/a", 300) == "memory://get/projects/x/a"
    assert [chunk async for chunk in storage.stream("projects/x/a")] == [b""]
    assert storage.expiries == [300, 300]


@pytest.mark.asyncio
async def test_download_requires_clean_state() -> None:
    """Changing the state gate would expose quarantined material."""
    from superboss.modules.files.models import File, FileState
    from superboss.modules.files.service import FileNotReadyError, FileService

    service = FileService(None, None)
    file = File(
        id=uuid4(),
        project_id=uuid4(),
        filename="report.pdf",
        category="资料",
        object_key="x",
        size_bytes=1,
        sha256="0" * 64,
        state=FileState.QUARANTINED,
        uploader_id=uuid4(),
    )
    with pytest.raises(FileNotReadyError):
        await service.ensure_downloadable(file)


@pytest.mark.asyncio
async def test_same_idempotency_key_reuses_one_active_multipart(db_session, active_owner) -> None:
    """A second identical start must not allocate another external upload."""
    from superboss.modules.files.schemas import UploadStart
    from superboss.modules.files.service import FileService

    storage = InMemoryObjectStorage()
    actor = Actor("user", active_owner.id, Role.OWNER, frozenset(), frozenset())
    project = Project(name="Files")
    db_session.add(project)
    await db_session.flush()
    command = UploadStart(
        project_id=project.id,
        filename="x.pdf",
        size_bytes=1,
        sha256="0" * 64,
        category="资料",
        file_date="2026-08-09",
    )
    service = FileService(db_session, storage)
    first = await service.start_upload(actor, command, "same")
    second = await service.start_upload(actor, command, "same")
    assert first.id == second.id
    assert len(storage.active) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"filename": "y.pdf"},
        {"category": "合同"},
        {"file_date": date(2026, 8, 10)},
        {"size_bytes": 2},
        {"sha256": "1" * 64},
        {"content_type": "image/png"},
    ],
)
async def test_same_key_with_changed_metadata_conflicts(db_session, active_owner, change) -> None:
    """A fingerprint omission would let one key name two different uploads."""
    from superboss.core.errors import ConflictError
    from superboss.modules.files.schemas import UploadStart
    from superboss.modules.files.service import FileService

    project = Project(name="Files conflict")
    db_session.add(project)
    await db_session.flush()
    actor = Actor("user", active_owner.id, Role.OWNER, frozenset(), frozenset())
    storage = InMemoryObjectStorage()
    service = FileService(db_session, storage)
    original = UploadStart(
        project_id=project.id,
        filename="x.pdf",
        size_bytes=1,
        sha256="0" * 64,
        category="资料",
        file_date="2026-08-09",
        content_type="application/pdf",
    )
    changed = original.model_copy(update=change)
    await service.start_upload(actor, original, "same")
    with pytest.raises(ConflictError):
        await service.start_upload(actor, changed, "same")
    assert len(storage.active) == 1


@pytest.mark.asyncio
async def test_same_key_is_scoped_to_project_and_actor(db_session, active_owner) -> None:
    """Global idempotency would wrongly join independent project/user uploads."""
    from superboss.modules.files.schemas import UploadStart
    from superboss.modules.files.service import FileService

    first_project, second_project = Project(name="Idem one"), Project(name="Idem two")
    staff = User(
        wecom_userid="file-staff", display_name="Staff", role=Role.STAFF, status=UserStatus.ACTIVE
    )
    db_session.add_all([first_project, second_project, staff])
    await db_session.flush()
    db_session.add(ProjectMember(project_id=second_project.id, user_id=staff.id))
    storage = InMemoryObjectStorage()
    service = FileService(db_session, storage)
    owner = Actor("user", active_owner.id, Role.OWNER, frozenset(), frozenset())
    staff_actor = Actor("user", staff.id, Role.STAFF, frozenset({second_project.id}), frozenset())

    def command(project_id):
        return UploadStart(
            project_id=project_id,
            filename="x.pdf",
            size_bytes=1,
            sha256="0" * 64,
            category="资料",
            file_date="2026-08-09",
        )

    first = await service.start_upload(owner, command(first_project.id), "same")
    second = await service.start_upload(owner, command(second_project.id), "same")
    third = await service.start_upload(staff_actor, command(second_project.id), "same")
    assert len({first.id, second.id, third.id}) == 3 and len(storage.active) == 3


@pytest.mark.asyncio
async def test_complete_sorts_parts_and_quarantines_without_enqueue(
    db_session, active_owner
) -> None:
    """Completion must persist quarantine, not use multipart ETags as checksums."""
    from superboss.modules.files.schemas import UploadStart
    from superboss.modules.files.service import FileService
    from superboss.modules.files.storage import CompletedPart

    project = Project(name="Complete")
    db_session.add(project)
    await db_session.flush()
    actor = Actor("user", active_owner.id, Role.OWNER, frozenset(), frozenset())
    enqueued: list[UUID] = []
    storage = InMemoryObjectStorage(complete_size=2)
    service = FileService(db_session, storage, lambda file_id: enqueued.append(file_id))
    upload = await service.start_upload(
        actor,
        UploadStart(
            project_id=project.id,
            filename="x.pdf",
            size_bytes=2,
            sha256="0" * 64,
            category="资料",
            file_date="2026-08-09",
        ),
        "complete",
    )
    file = await service.complete_upload(
        actor, upload.id, [CompletedPart(2, "not-a-sha"), CompletedPart(1, "0" * 64)]
    )
    assert file.state.value == "QUARANTINED" and file.sha256 == "0" * 64
    assert storage.completed[upload.multipart_id] == [
        CompletedPart(1, "0" * 64),
        CompletedPart(2, "not-a-sha"),
    ]
    assert enqueued == []


@pytest.mark.asyncio
async def test_size_mismatch_aborts_and_persists_failed(db_session, active_owner) -> None:
    from superboss.core.errors import FileUploadSizeMismatchError
    from superboss.modules.files.schemas import UploadStart
    from superboss.modules.files.service import FileService
    from superboss.modules.files.storage import CompletedPart

    project = Project(name="Mismatch")
    db_session.add(project)
    await db_session.flush()
    actor = Actor("user", active_owner.id, Role.OWNER, frozenset(), frozenset())
    storage = InMemoryObjectStorage(complete_size=2)
    service = FileService(db_session, storage)
    upload = await service.start_upload(
        actor,
        UploadStart(
            project_id=project.id,
            filename="x.pdf",
            size_bytes=1,
            sha256="0" * 64,
            category="资料",
            file_date="2026-08-09",
        ),
        "mismatch",
    )
    with pytest.raises(FileUploadSizeMismatchError):
        await service.complete_upload(actor, upload.id, [CompletedPart(1, "e")])
    await db_session.refresh(
        await db_session.get(
            __import__("superboss.modules.files.models", fromlist=["File"]).File, upload.file_id
        )
    )
    assert upload.multipart_id in storage.aborted


@pytest.mark.asyncio
async def test_part_presign_accepts_s3_boundaries(db_session, active_owner) -> None:
    """Changing the S3 boundary would reject valid first or last parts."""
    from superboss.modules.files.schemas import UploadStart
    from superboss.modules.files.service import FileService

    project = Project(name="Parts")
    db_session.add(project)
    await db_session.flush()
    storage = InMemoryObjectStorage()
    actor = Actor("user", active_owner.id, Role.OWNER, frozenset(), frozenset())
    service = FileService(db_session, storage)
    upload = await service.start_upload(
        actor,
        UploadStart(
            project_id=project.id,
            filename="x.pdf",
            size_bytes=1,
            sha256="0" * 64,
            category="资料",
            file_date="2026-08-09",
        ),
        "parts",
    )
    assert (await service.presign_part(actor, upload.id, 1)).endswith("/1")
    assert (await service.presign_part(actor, upload.id, 10_000)).endswith("/10000")
    assert storage.expiries == [900, 900]


@pytest.mark.asyncio
async def test_part_missing_upload_fails_closed(db_session, active_owner) -> None:
    from superboss.core.errors import NotFoundError
    from superboss.modules.files.service import FileService

    actor = Actor("user", active_owner.id, Role.OWNER, frozenset(), frozenset())
    with pytest.raises(NotFoundError):
        await FileService(db_session, InMemoryObjectStorage()).presign_part(actor, uuid4(), 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["QUARANTINED", "SCANNING", "CLEAN", "INFECTED", "FAILED"])
async def test_part_rejects_every_non_uploading_state(db_session, active_owner, state) -> None:
    from superboss.core.errors import ConflictError
    from superboss.modules.files.models import FileState
    from superboss.modules.files.schemas import UploadStart
    from superboss.modules.files.service import FileService

    project = Project(name=f"Part {state}")
    db_session.add(project)
    await db_session.flush()
    actor = Actor("user", active_owner.id, Role.OWNER, frozenset(), frozenset())
    service = FileService(db_session, InMemoryObjectStorage())
    upload = await service.start_upload(
        actor,
        UploadStart(
            project_id=project.id,
            filename="x.pdf",
            size_bytes=1,
            sha256="0" * 64,
            category="资料",
            file_date="2026-08-09",
        ),
        f"{state}-key",
    )
    file = await db_session.get(
        __import__("superboss.modules.files.models", fromlist=["File"]).File, upload.file_id
    )
    assert file is not None
    file.state = FileState(state)
    await db_session.flush()
    with pytest.raises(ConflictError):
        await service.presign_part(actor, upload.id, 1)


@pytest.mark.asyncio
async def test_foreign_staff_cannot_presign_part(db_session, active_owner) -> None:
    from superboss.core.errors import ForbiddenError
    from superboss.modules.files.schemas import UploadStart
    from superboss.modules.files.service import FileService

    target, assigned = Project(name="Target part"), Project(name="Assigned part")
    staff = User(
        wecom_userid="part-staff", display_name="Staff", role=Role.STAFF, status=UserStatus.ACTIVE
    )
    db_session.add_all([target, assigned, staff])
    await db_session.flush()
    db_session.add(ProjectMember(project_id=assigned.id, user_id=staff.id))
    storage = InMemoryObjectStorage()
    service = FileService(db_session, storage)
    owner = Actor("user", active_owner.id, Role.OWNER, frozenset(), frozenset())
    upload = await service.start_upload(
        owner,
        UploadStart(
            project_id=target.id,
            filename="x.pdf",
            size_bytes=1,
            sha256="0" * 64,
            category="资料",
            file_date="2026-08-09",
        ),
        "foreign",
    )
    staff_actor = Actor("user", staff.id, Role.STAFF, frozenset({assigned.id}), frozenset())
    with pytest.raises(ForbiddenError):
        await service.presign_part(staff_actor, upload.id, 1)
    assert storage.expiries == []


@pytest.mark.parametrize("key", ["x", "!" * 255, "", "x" * 256, " x", "x ", "x\r\ny", "中文"])
def test_idempotency_key_grammar(key: str) -> None:
    """Header keys are printable ASCII tokens, never whitespace or controls."""
    import re

    assert bool(re.fullmatch(r"[!-~]{1,255}", key)) == (key in {"x", "!" * 255})


@pytest.mark.asyncio
@pytest.mark.parametrize("abort_error", [None, RuntimeError("abort secret")])
async def test_storage_error_is_safe_and_leaves_prepared_file(
    db_session, active_owner, abort_error
) -> None:
    from sqlalchemy import select

    from superboss.core.errors import FileCompletionPendingError
    from superboss.modules.files.models import (
        File,
        FileState,
        FileStorageCleanup,
        FileUploadLifecycle,
    )
    from superboss.modules.files.schemas import UploadStart
    from superboss.modules.files.service import FileService
    from superboss.modules.files.storage import CompletedPart

    project = Project(name="Storage error")
    db_session.add(project)
    await db_session.flush()
    storage = InMemoryObjectStorage(
        complete_error=RuntimeError("S3 secret"), abort_error=abort_error
    )
    actor = Actor("user", active_owner.id, Role.OWNER, frozenset(), frozenset())
    service = FileService(db_session, storage)
    upload = await service.start_upload(
        actor,
        UploadStart(
            project_id=project.id,
            filename="x.pdf",
            size_bytes=1,
            sha256="0" * 64,
            category="资料",
            file_date="2026-08-09",
        ),
        "storage-error",
    )
    with pytest.raises(FileCompletionPendingError) as error:
        await service.complete_upload(actor, upload.id, [CompletedPart(1, "e")])
    assert "secret" not in str(error.value).lower()
    file = await db_session.get(File, upload.file_id)
    lifecycle = await db_session.get(FileUploadLifecycle, upload.id)
    assert file is not None and file.state == FileState.UPLOADING
    assert lifecycle is not None and lifecycle.completion_state == "PREPARED"
    assert lifecycle.completion_last_error_code == "COMPLETION_AMBIGUOUS"
    assert upload.multipart_id in storage.active and upload.multipart_id not in storage.aborted
    assert list(await db_session.scalars(select(FileStorageCleanup))) == []


@pytest.mark.asyncio
async def test_deleted_file_cascades_upload_and_operations_fail_closed(
    db_session, active_owner
) -> None:
    from superboss.core.errors import NotFoundError
    from superboss.modules.files.models import File
    from superboss.modules.files.schemas import UploadStart
    from superboss.modules.files.service import FileService
    from superboss.modules.files.storage import CompletedPart

    project = Project(name="Cascade")
    db_session.add(project)
    await db_session.flush()
    storage = InMemoryObjectStorage()
    actor = Actor("user", active_owner.id, Role.OWNER, frozenset(), frozenset())
    service = FileService(db_session, storage)
    upload = await service.start_upload(
        actor,
        UploadStart(
            project_id=project.id,
            filename="x.pdf",
            size_bytes=1,
            sha256="0" * 64,
            category="资料",
            file_date="2026-08-09",
        ),
        "cascade",
    )
    upload_id = upload.id
    file = await db_session.get(File, upload.file_id)
    assert file is not None
    await db_session.delete(file)
    await db_session.commit()
    db_session.expire_all()
    with pytest.raises(NotFoundError):
        await service.presign_part(actor, upload_id, 1)
    with pytest.raises(NotFoundError):
        await service.complete_upload(actor, upload_id, [CompletedPart(1, "e")])
    assert storage.expiries == [] and storage.completed == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["UPLOADING", "QUARANTINED", "SCANNING", "INFECTED", "FAILED"])
async def test_download_rejects_non_clean_state(db_session, active_owner, state) -> None:
    from superboss.modules.files.models import File, FileState
    from superboss.modules.files.service import FileNotReadyError, FileService

    project = Project(name=f"Download {state}")
    db_session.add(project)
    await db_session.flush()
    file = File(
        project_id=project.id,
        filename="secret.pdf",
        category="资料",
        file_date=date(2026, 8, 9),
        object_key="projects/x/secret",
        size_bytes=1,
        sha256="0" * 64,
        uploader_id=active_owner.id,
        uploader_kind="user",
        content_type="application/pdf",
        state=FileState(state),
    )
    db_session.add(file)
    await db_session.flush()
    storage = InMemoryObjectStorage()
    actor = Actor("user", active_owner.id, Role.OWNER, frozenset(), frozenset())
    with pytest.raises(FileNotReadyError):
        await FileService(db_session, storage).presign_download(actor, file.id)
    assert storage.expiries == []


@pytest.mark.asyncio
async def test_clean_download_owner_returns_key_url_with_short_expiry(
    db_session, active_owner
) -> None:
    from superboss.modules.files.models import File, FileState
    from superboss.modules.files.service import FileService

    project = Project(name="Clean download")
    db_session.add(project)
    await db_session.flush()
    file = File(
        project_id=project.id,
        filename="x.pdf",
        category="资料",
        file_date=date(2026, 8, 9),
        object_key="projects/clean/key",
        size_bytes=1,
        sha256="0" * 64,
        uploader_id=active_owner.id,
        uploader_kind="user",
        content_type="application/pdf",
        state=FileState.CLEAN,
    )
    db_session.add(file)
    await db_session.flush()
    storage = InMemoryObjectStorage()
    actor = Actor("user", active_owner.id, Role.OWNER, frozenset(), frozenset())
    assert (
        await FileService(db_session, storage).presign_download(actor, file.id)
        == "memory://get/projects/clean/key"
    )
    assert storage.expiries == [60]


@pytest.mark.asyncio
async def test_clean_download_assigned_staff_and_foreign_denial(db_session, active_owner) -> None:
    from superboss.core.errors import ForbiddenError
    from superboss.modules.files.models import File, FileState
    from superboss.modules.files.service import FileService

    project, other = Project(name="Staff download"), Project(name="Other download")
    staff = User(
        wecom_userid="download-staff",
        display_name="Staff",
        role=Role.STAFF,
        status=UserStatus.ACTIVE,
    )
    db_session.add_all([project, other, staff])
    await db_session.flush()
    db_session.add(ProjectMember(project_id=project.id, user_id=staff.id))
    file = File(
        project_id=project.id,
        filename="x.pdf",
        category="资料",
        file_date=date(2026, 8, 9),
        object_key="projects/staff/key",
        size_bytes=1,
        sha256="0" * 64,
        uploader_id=active_owner.id,
        uploader_kind="user",
        content_type="application/pdf",
        state=FileState.CLEAN,
    )
    db_session.add(file)
    await db_session.flush()
    storage = InMemoryObjectStorage()
    service = FileService(db_session, storage)
    assigned = Actor("user", staff.id, Role.STAFF, frozenset({project.id}), frozenset())
    assert await service.presign_download(assigned, file.id) == "memory://get/projects/staff/key"
    foreign = Actor("user", staff.id, Role.STAFF, frozenset({other.id}), frozenset())
    with pytest.raises(ForbiddenError):
        await service.presign_download(foreign, file.id)
    assert storage.expiries == [60]


@pytest.mark.asyncio
async def test_missing_download_file_fails_closed(db_session, active_owner) -> None:
    from superboss.core.errors import NotFoundError
    from superboss.modules.files.service import FileService

    storage = InMemoryObjectStorage()
    actor = Actor("user", active_owner.id, Role.OWNER, frozenset(), frozenset())
    with pytest.raises(NotFoundError):
        await FileService(db_session, storage).presign_download(actor, uuid4())
    assert storage.expiries == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,role",
    [
        ("device", None),
        ("system", None),
        ("device", Role.OWNER),
        ("system", Role.STAFF),
        ("user", None),
    ],
)
async def test_download_rejects_unsupported_actor_shapes(
    db_session, active_owner, kind, role
) -> None:
    from superboss.core.errors import ForbiddenError
    from superboss.modules.files.models import File, FileState
    from superboss.modules.files.service import FileService

    project = Project(name=f"Actor {kind} {role}")
    db_session.add(project)
    await db_session.flush()
    file = File(
        project_id=project.id,
        filename="x.pdf",
        category="资料",
        file_date=date(2026, 8, 9),
        object_key="projects/actor/key",
        size_bytes=1,
        sha256="0" * 64,
        uploader_id=active_owner.id,
        uploader_kind="user",
        content_type="application/pdf",
        state=FileState.CLEAN,
    )
    db_session.add(file)
    await db_session.flush()
    storage = InMemoryObjectStorage()
    with pytest.raises(ForbiddenError):
        await FileService(db_session, storage).presign_download(
            Actor(kind, active_owner.id, role, frozenset(), frozenset()), file.id
        )
    assert storage.expiries == []


@pytest.mark.asyncio
@pytest.mark.parametrize("round_number", range(3))
async def test_concurrent_same_metadata_reuses_winner_without_loser_multipart(
    db_session, active_owner, round_number: int
) -> None:
    import asyncio

    from sqlalchemy import func, select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from superboss.modules.files.models import File, Upload
    from superboss.modules.files.schemas import UploadStart
    from superboss.modules.files.service import FileService

    project = Project(name=f"Concurrent upload {round_number}")
    db_session.add(project)
    await db_session.commit()
    actor = Actor("user", active_owner.id, Role.OWNER, frozenset(), frozenset())
    command = UploadStart(
        project_id=project.id,
        filename="x.pdf",
        size_bytes=1,
        sha256="0" * 64,
        category="资料",
        file_date="2026-08-09",
    )
    storage = InMemoryObjectStorage()
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    barrier = asyncio.Barrier(2)

    async def create() -> tuple[UUID, UUID]:
        async with factory() as session:
            await barrier.wait()
            upload = await FileService(session, storage).start_upload(
                actor, command, f"race-{round_number}"
            )
            await session.commit()
            return upload.id, upload.file_id

    first, second = await asyncio.wait_for(asyncio.gather(create(), create()), timeout=10)
    assert (
        first == second
        and len(storage.active) == 1
        and storage.create_calls == 1
        and storage.aborted == set()
    )
    assert await db_session.scalar(select(func.count()).select_from(Upload)) == 1
    assert await db_session.scalar(select(func.count()).select_from(File)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("round_number", range(3))
async def test_concurrent_different_metadata_conflicts_without_loser_multipart(
    db_session, active_owner, round_number: int
) -> None:
    import asyncio

    from sqlalchemy import func, select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from superboss.core.errors import ConflictError
    from superboss.modules.files.models import Upload
    from superboss.modules.files.schemas import UploadStart
    from superboss.modules.files.service import FileService

    project = Project(name=f"Concurrent conflict {round_number}")
    db_session.add(project)
    await db_session.commit()
    actor = Actor("user", active_owner.id, Role.OWNER, frozenset(), frozenset())
    first = UploadStart(
        project_id=project.id,
        filename="x.pdf",
        size_bytes=1,
        sha256="0" * 64,
        category="资料",
        file_date="2026-08-09",
        content_type="application/pdf",
    )
    second = first.model_copy(update={"content_type": "image/png"})
    storage = InMemoryObjectStorage()
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    barrier = asyncio.Barrier(2)

    async def create(command: UploadStart):
        async with factory() as session:
            try:
                await barrier.wait()
                upload = await FileService(session, storage).start_upload(
                    actor, command, f"race-conflict-{round_number}"
                )
                await session.commit()
                return upload
            except ConflictError:
                return None

    results = await asyncio.wait_for(asyncio.gather(create(first), create(second)), timeout=10)
    winner = next(item for item in results if item is not None)
    assert (
        sum(item is not None for item in results) == 1
        and len(storage.active) == 1
        and storage.create_calls == 1
        and storage.aborted == set()
    )
    saved = await db_session.get(Upload, winner.id)
    assert (
        saved is not None and await db_session.scalar(select(func.count()).select_from(Upload)) == 1
    )


@pytest.mark.asyncio
async def test_empty_multipart_response_leaves_safe_durable_provisioning(
    db_session, active_owner
) -> None:
    from sqlalchemy import select

    from superboss.core.errors import DomainError
    from superboss.modules.files.models import FileUploadLifecycle, Upload
    from superboss.modules.files.schemas import UploadStart
    from superboss.modules.files.service import FileService

    project = Project(name="Empty multipart response")
    db_session.add(project)
    await db_session.flush()
    storage = InMemoryObjectStorage(created_multipart_id="")
    actor = Actor("user", active_owner.id, Role.OWNER, frozenset(), frozenset())
    with pytest.raises(DomainError) as error:
        await FileService(db_session, storage).start_upload(
            actor,
            UploadStart(
                project_id=project.id,
                filename="x.pdf",
                size_bytes=1,
                sha256="0" * 64,
                category="资料",
                file_date="2026-08-09",
            ),
            "constraint",
        )
    assert (
        error.value.code == "FILE_PROVISIONING_PENDING" and "secret" not in str(error.value).lower()
    )
    upload = await db_session.scalar(select(Upload))
    lifecycle = await db_session.scalar(select(FileUploadLifecycle))
    assert upload is not None and lifecycle is not None and upload.multipart_id is None
    assert lifecycle.provision_state == "PROVISIONING" and "" in storage.active


@pytest.mark.asyncio
async def test_concurrent_complete_locks_upload_before_storage_completion(
    db_session, active_owner
) -> None:
    """Without a row lock both sessions pass the state check and complete the same upload."""
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from superboss.core.errors import FileCompletionPendingError
    from superboss.modules.files.models import File, FileState
    from superboss.modules.files.schemas import UploadStart
    from superboss.modules.files.service import FileService, FileUploadNotActiveError
    from superboss.modules.files.storage import CompletedPart, ObjectMetadata

    class RacingCompleteStorage(InMemoryObjectStorage):
        def __init__(self) -> None:
            super().__init__()
            self.complete_calls = 0
            self.second_complete_arrived = asyncio.Event()

        async def complete_multipart(
            self, object_key: str, multipart_id: str, parts: list[CompletedPart]
        ) -> ObjectMetadata:
            del multipart_id, parts
            self.complete_calls += 1
            if self.complete_calls == 1:
                try:
                    await asyncio.wait_for(self.second_complete_arrived.wait(), timeout=0.5)
                except TimeoutError:
                    pass
            else:
                self.second_complete_arrived.set()
            metadata = ObjectMetadata(size_bytes=1)
            self.objects[object_key] = metadata
            return metadata

    actor = Actor("user", active_owner.id, Role.OWNER, frozenset(), frozenset())
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    for round_number in range(3):
        project = Project(name=f"Complete race {round_number}")
        db_session.add(project)
        await db_session.commit()
        storage = RacingCompleteStorage()
        upload = await FileService(db_session, storage).start_upload(
            actor,
            UploadStart(
                project_id=project.id,
                filename="x.pdf",
                size_bytes=1,
                sha256="0" * 64,
                category="资料",
                file_date="2026-08-09",
            ),
            f"complete-race-{round_number}",
        )
        await db_session.commit()
        file_id = upload.file_id
        barrier = asyncio.Barrier(2)

        async def complete_once(
            race_barrier: asyncio.Barrier = barrier,
            race_storage: RacingCompleteStorage = storage,
            upload_id: UUID = upload.id,
        ) -> bool:
            async with factory() as session:
                await race_barrier.wait()
                try:
                    await FileService(session, race_storage).complete_upload(
                        actor, upload_id, [CompletedPart(1, "etag")]
                    )
                    await session.commit()
                    return True
                except (FileCompletionPendingError, FileUploadNotActiveError):
                    await session.rollback()
                    return False

        results = await asyncio.wait_for(
            asyncio.gather(complete_once(), complete_once()), timeout=5
        )
        db_session.expire_all()
        file = await db_session.get(File, file_id)
        assert any(results)
        assert storage.complete_calls == 1
        assert file is not None and file.state == FileState.QUARANTINED


@pytest.mark.asyncio
async def test_corrupt_cross_project_upload_fails_before_part_or_completion_storage(
    db_session, active_owner
) -> None:
    """Legacy project drift must not let B-authorized actors operate a file owned by A."""
    from sqlalchemy import select, text

    from superboss.modules.files.models import File, FileState, Upload
    from superboss.modules.files.service import FileService, FileUploadConflictError
    from superboss.modules.files.storage import CompletedPart

    project_a = Project(name="Legacy file project A")
    project_b = Project(name="Legacy file project B")
    staff = User(
        wecom_userid="legacy-project-staff",
        display_name="Staff",
        role=Role.STAFF,
        status=UserStatus.ACTIVE,
    )
    db_session.add_all([project_a, project_b, staff])
    await db_session.flush()
    db_session.add(ProjectMember(project_id=project_b.id, user_id=staff.id))
    file = File(
        project_id=project_a.id,
        filename="a.pdf",
        category="资料",
        file_date=date(2026, 8, 9),
        object_key=f"projects/{project_a.id}/legacy/a.pdf",
        size_bytes=1,
        sha256="0" * 64,
        state=FileState.UPLOADING,
        uploader_id=active_owner.id,
        uploader_kind="user",
        content_type="application/pdf",
    )
    db_session.add(file)
    await db_session.flush()
    await db_session.execute(text("SET session_replication_role = replica"))
    db_session.add(
        Upload(
            file_id=file.id,
            project_id=project_b.id,
            uploader_id=active_owner.id,
            uploader_kind="user",
            idempotency_key="legacy-cross-project",
            metadata_fingerprint="0" * 64,
            multipart_id="legacy-multipart",
        )
    )
    await db_session.commit()
    await db_session.execute(text("SET session_replication_role = origin"))

    storage = InMemoryObjectStorage()
    service = FileService(db_session, storage)
    staff_actor = Actor("user", staff.id, Role.STAFF, frozenset({project_b.id}), frozenset())
    owner_actor = Actor("user", active_owner.id, Role.OWNER, frozenset(), frozenset())
    upload = await db_session.scalar(
        select(Upload).where(Upload.idempotency_key == "legacy-cross-project")
    )
    assert upload is not None
    upload_id = upload.id
    for actor in (staff_actor, owner_actor):
        with pytest.raises(FileUploadConflictError):
            await service.presign_part(actor, upload_id, 1)
        with pytest.raises(FileUploadConflictError):
            await service.complete_upload(actor, upload_id, [CompletedPart(1, "etag")])
        await db_session.rollback()
    assert storage.expiries == [] and storage.completed == {}


@pytest.mark.asyncio
async def test_start_commits_provisioning_intent_before_creating_multipart(
    db_session, active_owner
) -> None:
    """Moving the database commit below create_multipart loses crash recovery intent."""
    from sqlalchemy import func, select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from superboss.modules.files.models import File, FileUploadLifecycle, Upload
    from superboss.modules.files.schemas import UploadStart
    from superboss.modules.files.service import FileService

    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    class ObservingStorage(InMemoryObjectStorage):
        observed: tuple[int, int, int, str | None, str | None] | None = None

        async def create_multipart(self, object_key: str, content_type: str) -> str:
            async with factory() as reader:
                upload = await reader.scalar(select(Upload))
                lifecycle = await reader.scalar(select(FileUploadLifecycle))
                self.observed = (
                    await reader.scalar(select(func.count()).select_from(File)) or 0,
                    await reader.scalar(select(func.count()).select_from(Upload)) or 0,
                    await reader.scalar(select(func.count()).select_from(FileUploadLifecycle)) or 0,
                    upload.multipart_id if upload is not None else None,
                    lifecycle.provision_state if lifecycle is not None else None,
                )
            return await super().create_multipart(object_key, content_type)

    project = Project(name="Provision intent")
    db_session.add(project)
    await db_session.commit()
    actor = Actor("user", active_owner.id, Role.OWNER, frozenset(), frozenset())
    storage = ObservingStorage()
    upload = await FileService(db_session, storage).start_upload(
        actor,
        UploadStart(
            project_id=project.id,
            filename="x.pdf",
            size_bytes=1,
            sha256="0" * 64,
            category="docs",
            file_date="2026-08-09",
        ),
        "provision-intent",
    )

    assert storage.observed == (1, 1, 1, None, "PROVISIONING")
    lifecycle = await db_session.get(FileUploadLifecycle, upload.id)
    assert lifecycle is not None and lifecycle.provision_state == "READY"
    assert upload.multipart_id is not None and lifecycle.multipart_id == upload.multipart_id


@pytest.mark.asyncio
async def test_start_replay_recovers_multipart_after_create_response_is_lost(
    db_session, active_owner
) -> None:
    """A created multipart whose response is lost must be discovered, not created again."""
    from sqlalchemy import select

    from superboss.core.errors import DomainError
    from superboss.modules.files.models import FileUploadLifecycle, Upload
    from superboss.modules.files.schemas import UploadStart
    from superboss.modules.files.service import FileService

    class ResponseLostStorage(InMemoryObjectStorage):
        response_lost = False

        async def create_multipart(self, object_key: str, content_type: str) -> str:
            multipart_id = await super().create_multipart(object_key, content_type)
            if not self.response_lost:
                self.response_lost = True
                raise RuntimeError("provider response secret")
            return multipart_id

        async def list_multipart_uploads(self, object_key: str) -> list[str]:
            return sorted(upload_id for upload_id, key in self.active.items() if key == object_key)

    project = Project(name="Provision recovery")
    db_session.add(project)
    await db_session.commit()
    actor = Actor("user", active_owner.id, Role.OWNER, frozenset(), frozenset())
    command = UploadStart(
        project_id=project.id,
        filename="x.pdf",
        size_bytes=1,
        sha256="0" * 64,
        category="docs",
        file_date="2026-08-09",
    )
    storage = ResponseLostStorage()
    service = FileService(db_session, storage)

    with pytest.raises(DomainError) as pending:
        await service.start_upload(actor, command, "response-lost")
    assert pending.value.code == "FILE_PROVISIONING_PENDING"
    lifecycle = await db_session.scalar(select(FileUploadLifecycle))
    upload = await db_session.scalar(select(Upload))
    assert lifecycle is not None and upload is not None
    assert lifecycle.provision_state == "PROVISIONING" and upload.multipart_id is None
    assert "secret" not in str(pending.value).lower() and len(storage.active) == 1

    recovered = await service.start_upload(actor, command, "response-lost")
    assert recovered.id == upload.id and recovered.multipart_id in storage.active
    assert storage.create_calls == 1
    await db_session.refresh(lifecycle)
    assert lifecycle.provision_state == "READY" and lifecycle.multipart_id == recovered.multipart_id


@pytest.mark.asyncio
async def test_start_records_cleanup_for_multiple_exact_key_multiparts(
    db_session, active_owner
) -> None:
    """Choosing one of several active uploads silently would orphan the others."""
    from sqlalchemy import select

    from superboss.core.errors import DomainError
    from superboss.modules.files.models import FileStorageCleanup, FileUploadLifecycle, Upload
    from superboss.modules.files.schemas import UploadStart
    from superboss.modules.files.service import FileService

    class MultipleActiveStorage(InMemoryObjectStorage):
        async def list_multipart_uploads(self, object_key: str) -> list[str]:
            return ["multipart-a", "multipart-b"]

        async def create_multipart(self, object_key: str, content_type: str) -> str:
            raise AssertionError("exact-key discovery must run before create")

    project = Project(name="Provision duplicates")
    db_session.add(project)
    await db_session.commit()
    actor = Actor("user", active_owner.id, Role.OWNER, frozenset(), frozenset())
    command = UploadStart(
        project_id=project.id,
        filename="x.pdf",
        size_bytes=1,
        sha256="0" * 64,
        category="docs",
        file_date="2026-08-09",
    )

    with pytest.raises(DomainError) as pending:
        await FileService(db_session, MultipleActiveStorage()).start_upload(
            actor, command, "duplicates"
        )
    assert pending.value.code == "FILE_PROVISIONING_PENDING"
    lifecycle = await db_session.scalar(select(FileUploadLifecycle))
    upload = await db_session.scalar(select(Upload))
    cleanup = list((await db_session.scalars(select(FileStorageCleanup))).all())
    assert (
        lifecycle is not None and upload is not None and lifecycle.provision_state == "PROVISIONING"
    )
    assert upload.multipart_id is None
    assert {item.multipart_id for item in cleanup} == {"multipart-a", "multipart-b"}
    assert all(
        item.operation == "ABORT_MULTIPART" and item.last_error_code is None for item in cleanup
    )


@pytest.mark.asyncio
async def test_start_replay_recovers_after_exact_key_listing_failure(
    db_session, active_owner
) -> None:
    """A transient list failure must retain the durable intent for the identical replay."""
    from sqlalchemy import select

    from superboss.core.errors import DomainError
    from superboss.modules.files.models import FileUploadLifecycle, Upload
    from superboss.modules.files.schemas import UploadStart
    from superboss.modules.files.service import FileService

    project = Project(name="Provision list retry")
    db_session.add(project)
    await db_session.commit()
    actor = Actor("user", active_owner.id, Role.OWNER, frozenset(), frozenset())
    command = UploadStart(
        project_id=project.id,
        filename="x.pdf",
        size_bytes=1,
        sha256="0" * 64,
        category="docs",
        file_date="2026-08-09",
    )
    storage = InMemoryObjectStorage(list_error=RuntimeError("provider list secret"))
    service = FileService(db_session, storage)

    with pytest.raises(DomainError) as pending:
        await service.start_upload(actor, command, "list-retry")
    assert pending.value.code == "FILE_PROVISIONING_PENDING"
    upload = await db_session.scalar(select(Upload))
    lifecycle = await db_session.scalar(select(FileUploadLifecycle))
    assert upload is not None and lifecycle is not None
    assert upload.multipart_id is None and lifecycle.provision_state == "PROVISIONING"
    assert storage.active == {} and "secret" not in str(pending.value).lower()

    storage.list_error = None
    recovered = await service.start_upload(actor, command, "list-retry")
    assert recovered.id == upload.id and recovered.multipart_id is not None
    assert storage.create_calls == 1 and storage.list_calls == 2


@pytest.mark.asyncio
async def test_completion_replay_finalizes_once_and_persists_safe_outbox(
    db_session, active_owner
) -> None:
    """Prepared canonical parts recover to one quarantine transition and two opaque jobs."""
    from sqlalchemy import select

    from superboss.modules.files.models import (
        File,
        FileLifecycleOutbox,
        FileState,
        FileUploadLifecycle,
    )
    from superboss.modules.files.schemas import UploadStart
    from superboss.modules.files.service import FileService
    from superboss.modules.files.storage import CompletedPart

    project = Project(name="Completion recovery")
    db_session.add(project)
    await db_session.commit()
    actor = Actor("user", active_owner.id, Role.OWNER, frozenset(), frozenset())
    storage = InMemoryObjectStorage(complete_size=2)
    service = FileService(db_session, storage)
    upload = await service.start_upload(
        actor,
        UploadStart(
            project_id=project.id,
            filename="replay.pdf",
            category="docs",
            file_date="2026-08-09",
            size_bytes=2,
            sha256="0" * 64,
        ),
        "completion-replay",
    )
    request_id = uuid4()
    parts = [CompletedPart(2, "etag-2"), CompletedPart(1, "etag-1")]

    first = await service.complete_upload(actor, upload.id, parts, request_id)
    second = await service.complete_upload(actor, upload.id, list(reversed(parts)), request_id)

    lifecycle = await db_session.get(FileUploadLifecycle, upload.id)
    outbox = list(
        await db_session.scalars(select(FileLifecycleOutbox).order_by(FileLifecycleOutbox.kind))
    )
    assert first.id == second.id == upload.file_id
    assert first.state == FileState.QUARANTINED
    assert lifecycle is not None and lifecycle.completion_state == "QUARANTINED"
    assert lifecycle.canonical_parts_json == [
        {"part_number": 1, "etag": "etag-1"},
        {"part_number": 2, "etag": "etag-2"},
    ]
    assert lifecycle.completion_actor_id == active_owner.id
    assert lifecycle.completion_request_id == request_id
    assert len(storage.completed) == 1
    assert [(entry.kind, entry.file_id, entry.project_id) for entry in outbox] == [
        ("completion_audit", upload.file_id, project.id),
        ("scan_dispatch", upload.file_id, project.id),
    ]
    assert {column.name for column in FileLifecycleOutbox.__table__.columns} == {
        "id",
        "kind",
        "dedupe_key",
        "file_id",
        "project_id",
        "state",
        "attempt_count",
        "next_attempt_at",
            "locked_at",
            "claim_token",
            "last_error_code",
        "created_at",
        "updated_at",
    }
    persisted_file = await db_session.get(File, upload.file_id)
    assert persisted_file is not None and persisted_file.state == FileState.QUARANTINED


@pytest.mark.asyncio
async def test_memory_storage_abort_never_deletes_completed_object() -> None:
    """Compensation must explicitly delete an object after aborting its multipart."""
    from superboss.modules.files.storage import CompletedPart

    storage = InMemoryObjectStorage()
    key = "projects/example/docs/2026-08-09/file/example.pdf"
    multipart_id = await storage.create_multipart(key, "application/pdf")
    await storage.complete_multipart(key, multipart_id, [CompletedPart(1, "etag")])
    await storage.abort_multipart(key, multipart_id)

    assert await storage.stat_object(key) is not None
    await storage.delete_object(key)
    assert await storage.stat_object(key) is None


@pytest.mark.asyncio
async def test_size_mismatch_persists_compensation_and_reconciler_retries_delete(
    db_session, active_owner
) -> None:
    """A completed wrong-size object is deleted only through a durable retry record."""
    from datetime import UTC, datetime

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from superboss.core.errors import FileUploadSizeMismatchError
    from superboss.modules.files.models import (
        File,
        FileState,
        FileStorageCleanup,
        FileUploadLifecycle,
    )
    from superboss.modules.files.schemas import UploadStart
    from superboss.modules.files.service import FileLifecycleService, FileService
    from superboss.modules.files.storage import CompletedPart

    project = Project(name="Compensation retry")
    db_session.add(project)
    await db_session.commit()
    actor = Actor("user", active_owner.id, Role.OWNER, frozenset(), frozenset())
    storage = InMemoryObjectStorage(complete_size=2, delete_error=RuntimeError("delete secret"))
    upload = await FileService(db_session, storage).start_upload(
        actor,
        UploadStart(
            project_id=project.id,
            filename="wrong-size.pdf",
            category="docs",
            file_date="2026-08-09",
            size_bytes=1,
            sha256="0" * 64,
        ),
        "compensation-retry",
    )
    with pytest.raises(FileUploadSizeMismatchError):
        await FileService(db_session, storage).complete_upload(
            actor, upload.id, [CompletedPart(1, "etag")]
        )

    file = await db_session.get(File, upload.file_id)
    lifecycle = await db_session.get(FileUploadLifecycle, upload.id)
    cleanup = list(
        await db_session.scalars(select(FileStorageCleanup).order_by(FileStorageCleanup.operation))
    )
    assert file is not None and file.state == FileState.FAILED
    assert lifecycle is not None and lifecycle.completion_state == "COMPENSATION_PENDING"
    assert [entry.operation for entry in cleanup] == ["ABORT_MULTIPART", "DELETE_OBJECT"]
    assert cleanup[1].state == "PENDING" and cleanup[1].last_error_code == "DELETE_FAILED"
    assert "secret" not in str(cleanup[1].last_error_code)
    assert await storage.stat_object(file.object_key) is not None
    object_key = file.object_key

    storage.delete_error = None
    cleanup[1].next_attempt_at = datetime.now(UTC)
    await db_session.commit()
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    assert await FileLifecycleService(factory, storage).reconcile() == 1
    db_session.expire_all()
    cleanup = list(
        await db_session.scalars(select(FileStorageCleanup).order_by(FileStorageCleanup.operation))
    )
    assert [entry.state for entry in cleanup] == ["DONE", "DONE"]
    assert await storage.stat_object(object_key) is None


@pytest.mark.asyncio
async def test_reconciler_finalizes_durable_prepared_object_without_second_complete(
    db_session, active_owner
) -> None:
    """A crash after provider completion is recoverable without a client replay."""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from superboss.modules.files.models import File, FileState, FileUploadLifecycle
    from superboss.modules.files.schemas import UploadStart
    from superboss.modules.files.service import FileLifecycleService, FileService
    from superboss.modules.files.storage import CompletedPart

    project = Project(name="Prepared recovery")
    db_session.add(project)
    await db_session.commit()
    actor = Actor("user", active_owner.id, Role.OWNER, frozenset(), frozenset())
    storage = InMemoryObjectStorage(complete_size=1)
    upload = await FileService(db_session, storage).start_upload(
        actor,
        UploadStart(
            project_id=project.id,
            filename="prepared.pdf",
            category="docs",
            file_date="2026-08-09",
            size_bytes=1,
            sha256="0" * 64,
        ),
        "prepared-recovery",
    )
    lifecycle = await db_session.get(FileUploadLifecycle, upload.id)
    assert lifecycle is not None and upload.multipart_id is not None
    lifecycle.completion_state = "PREPARED"
    lifecycle.parts_digest = "a" * 64
    lifecycle.canonical_parts_json = [{"part_number": 1, "etag": "etag"}]
    lifecycle.completion_event_key = uuid4()
    lifecycle.prepared_at = datetime.now(UTC) - timedelta(seconds=121)
    await db_session.commit()
    await storage.complete_multipart(
        lifecycle.object_key, upload.multipart_id, [CompletedPart(1, "etag")]
    )
    assert len(storage.completed) == 1
    file_id = upload.file_id
    upload_id = upload.id

    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    assert await FileLifecycleService(factory, storage).reconcile() == 1
    db_session.expire_all()
    file = await db_session.get(File, file_id)
    lifecycle = await db_session.get(FileUploadLifecycle, upload_id)
    assert file is not None and file.state == FileState.QUARANTINED
    assert lifecycle is not None and lifecycle.completion_state == "QUARANTINED"
    assert len(storage.completed) == 1


@pytest.mark.asyncio
async def test_completion_delivery_retries_same_durable_scan_key_after_dispatch_failure(
    db_session, active_owner
) -> None:
    """A failed dispatcher leaves audit immutable and retries the same idempotency key."""
    from datetime import UTC, datetime

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from superboss.modules.audit.models import AuditLog
    from superboss.modules.files.models import FileLifecycleOutbox
    from superboss.modules.files.schemas import UploadStart
    from superboss.modules.files.service import FileLifecycleService, FileService
    from superboss.modules.files.storage import CompletedPart

    project = Project(name="Delivery retry")
    db_session.add(project)
    await db_session.commit()
    actor = Actor("user", active_owner.id, Role.OWNER, frozenset(), frozenset())
    storage = InMemoryObjectStorage()
    upload = await FileService(db_session, storage).start_upload(
        actor,
        UploadStart(
            project_id=project.id,
            filename="delivery.pdf",
            category="docs",
            file_date="2026-08-09",
            size_bytes=1,
            sha256="0" * 64,
        ),
        "delivery-retry",
    )
    await FileService(db_session, storage).complete_upload(
        actor, upload.id, [CompletedPart(1, "etag")], uuid4()
    )
    upload_id = upload.id
    file_id = upload.file_id
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    def fail_dispatch(_file_id: UUID, _delivery_key: UUID) -> None:
        raise RuntimeError("dispatcher secret")

    assert not await FileLifecycleService(factory, storage, fail_dispatch).deliver_completion(
        upload_id
    )
    db_session.expire_all()
    jobs = list(
        await db_session.scalars(select(FileLifecycleOutbox).order_by(FileLifecycleOutbox.kind))
    )
    audits = list(await db_session.scalars(select(AuditLog)))
    assert [job.state for job in jobs] == ["DELIVERED", "PENDING"]
    assert jobs[1].last_error_code == "DISPATCH_FAILED"
    assert len(audits) == 1 and "secret" not in str(jobs[1].last_error_code)
    jobs[1].next_attempt_at = datetime.now(UTC)
    await db_session.commit()

    seen: list[tuple[UUID, UUID]] = []
    assert await FileLifecycleService(
        factory, storage, lambda file_id, key: seen.append((file_id, key))
    ).deliver_completion(upload_id)
    db_session.expire_all()
    jobs = list(
        await db_session.scalars(select(FileLifecycleOutbox).order_by(FileLifecycleOutbox.kind))
    )
    assert [job.state for job in jobs] == ["DELIVERED", "DELIVERED"]
    assert seen == [(file_id, jobs[1].dedupe_key)]
    assert len(list(await db_session.scalars(select(AuditLog)))) == 1


@pytest.mark.asyncio
async def test_database_file_delete_snapshots_cleanup_before_cascade(
    db_session, active_owner
) -> None:
    """The database trigger preserves storage coordinates after ORM file deletion."""
    from datetime import UTC, datetime

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from superboss.modules.files.models import File, FileStorageCleanup, Upload
    from superboss.modules.files.schemas import UploadStart
    from superboss.modules.files.service import FileLifecycleService, FileService

    project = Project(name="Delete cleanup")
    db_session.add(project)
    await db_session.commit()
    actor = Actor("user", active_owner.id, Role.OWNER, frozenset(), frozenset())
    storage = InMemoryObjectStorage()
    upload = await FileService(db_session, storage).start_upload(
        actor,
        UploadStart(
            project_id=project.id,
            filename="delete.pdf",
            category="docs",
            file_date="2026-08-09",
            size_bytes=1,
            sha256="0" * 64,
        ),
        "delete-cleanup",
    )
    file = await db_session.get(File, upload.file_id)
    assert file is not None and upload.multipart_id is not None
    object_key = file.object_key
    upload_id = upload.id
    await db_session.delete(file)
    await db_session.commit()
    db_session.expire_all()
    db_session.expire_all()

    jobs = list(
        await db_session.scalars(select(FileStorageCleanup).order_by(FileStorageCleanup.operation))
    )
    assert [job.operation for job in jobs] == ["ABORT_MULTIPART", "DELETE_OBJECT"]
    assert all(job.state == "PENDING" and job.object_key == object_key for job in jobs)
    assert await db_session.get(Upload, upload_id) is None
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    assert await FileLifecycleService(factory, storage).reconcile() == 0
    for job in jobs:
        job.next_attempt_at = datetime.now(UTC)
    await db_session.commit()
    assert await FileLifecycleService(factory, storage).reconcile() == 2
    db_session.expire_all()
    jobs = list(
        await db_session.scalars(select(FileStorageCleanup).order_by(FileStorageCleanup.operation))
    )
    assert [job.state for job in jobs] == ["DONE", "DONE"]
    assert storage.active == {}


@pytest.mark.asyncio
async def test_raw_sql_file_delete_snapshots_active_multipart_cleanup(
    db_session, active_owner
) -> None:
    """A raw SQL delete cannot bypass the database cleanup trigger."""
    from datetime import UTC, datetime

    from sqlalchemy import select, text
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from superboss.modules.files.models import File, FileStorageCleanup, Upload
    from superboss.modules.files.schemas import UploadStart
    from superboss.modules.files.service import FileLifecycleService, FileService

    project = Project(name="Raw delete cleanup")
    db_session.add(project)
    await db_session.commit()
    storage = InMemoryObjectStorage()
    upload = await FileService(db_session, storage).start_upload(
        Actor("user", active_owner.id, Role.OWNER, frozenset(), frozenset()),
        UploadStart(
            project_id=project.id,
            filename="raw.pdf",
            category="docs",
            file_date="2026-08-09",
            size_bytes=1,
            sha256="0" * 64,
        ),
        "raw-delete",
    )
    file = await db_session.get(File, upload.file_id)
    assert file is not None
    file_id = file.id
    upload_id = upload.id
    await db_session.execute(text("DELETE FROM files WHERE id = :id"), {"id": file_id})
    await db_session.commit()
    db_session.expire_all()
    jobs = list(
        await db_session.scalars(select(FileStorageCleanup).order_by(FileStorageCleanup.operation))
    )
    assert (
        await db_session.get(File, file_id) is None
        and await db_session.get(Upload, upload_id) is None
    )
    assert [job.operation for job in jobs] == ["ABORT_MULTIPART", "DELETE_OBJECT"]
    assert len({(job.operation, job.object_key, job.multipart_id) for job in jobs}) == 2
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    assert await FileLifecycleService(factory, storage).reconcile() == 0
    for job in jobs:
        job.next_attempt_at = datetime.now(UTC)
    await db_session.commit()
    assert await FileLifecycleService(factory, storage).reconcile() == 2
    assert storage.active == {}


@pytest.mark.asyncio
async def test_project_cascade_snapshots_active_and_completed_file_cleanup(
    db_session, active_owner
) -> None:
    """Project cascade leaves cleanup for both active multipart and completed object."""
    from datetime import UTC, datetime

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from superboss.modules.files.models import File, FileStorageCleanup
    from superboss.modules.files.schemas import UploadStart
    from superboss.modules.files.service import FileLifecycleService, FileService
    from superboss.modules.files.storage import CompletedPart

    project = Project(name="Project cascade cleanup")
    db_session.add(project)
    await db_session.commit()
    actor = Actor("user", active_owner.id, Role.OWNER, frozenset(), frozenset())
    storage = InMemoryObjectStorage()
    service = FileService(db_session, storage)
    active = await service.start_upload(
        actor,
        UploadStart(
            project_id=project.id,
            filename="active.pdf",
            category="docs",
            file_date="2026-08-09",
            size_bytes=1,
            sha256="0" * 64,
        ),
        "cascade-active",
    )
    completed = await service.start_upload(
        actor,
        UploadStart(
            project_id=project.id,
            filename="done.pdf",
            category="docs",
            file_date="2026-08-09",
            size_bytes=1,
            sha256="1" * 64,
        ),
        "cascade-done",
    )
    await service.complete_upload(actor, completed.id, [CompletedPart(1, "etag")], uuid4())
    active_file = await db_session.get(File, active.file_id)
    completed_file = await db_session.get(File, completed.file_id)
    assert active_file is not None and completed_file is not None
    active_key = active_file.object_key
    completed_key = completed_file.object_key
    await db_session.delete(project)
    await db_session.commit()
    db_session.expire_all()
    jobs = list(await db_session.scalars(select(FileStorageCleanup)))
    assert len(jobs) == 4 and await storage.stat_object(completed_key) is not None
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    assert await FileLifecycleService(factory, storage).reconcile() == 2
    active_jobs = [job for job in jobs if job.object_key == active_key]
    assert len(active_jobs) == 2 and all(job.state == "PENDING" for job in active_jobs)
    for job in active_jobs:
        job.next_attempt_at = datetime.now(UTC)
    await db_session.commit()
    assert await FileLifecycleService(factory, storage).reconcile() == 2
    assert storage.active == {} and await storage.stat_object(completed_key) is None


@pytest.mark.asyncio
async def test_file_delete_rollback_leaves_no_cleanup_snapshot(db_session, active_owner) -> None:
    """Trigger rows share the deleting transaction and disappear on rollback."""
    from sqlalchemy import select

    from superboss.modules.files.models import File, FileStorageCleanup, Upload
    from superboss.modules.files.schemas import UploadStart
    from superboss.modules.files.service import FileService

    project = Project(name="Rollback cleanup")
    db_session.add(project)
    await db_session.commit()
    storage = InMemoryObjectStorage()
    upload = await FileService(db_session, storage).start_upload(
        Actor("user", active_owner.id, Role.OWNER, frozenset(), frozenset()),
        UploadStart(
            project_id=project.id,
            filename="rollback.pdf",
            category="docs",
            file_date="2026-08-09",
            size_bytes=1,
            sha256="0" * 64,
        ),
        "rollback-delete",
    )
    file = await db_session.get(File, upload.file_id)
    assert file is not None
    file_id = file.id
    upload_id = upload.id
    await db_session.delete(file)
    await db_session.flush()
    await db_session.rollback()
    assert await db_session.get(File, file_id) is not None
    assert await db_session.get(Upload, upload_id) is not None
    assert list(await db_session.scalars(select(FileStorageCleanup))) == []
    assert storage.active


@pytest.mark.asyncio
async def test_cancel_requested_lifecycle_never_provisions_or_completes(
    db_session, active_owner
) -> None:
    """A retained legacy cancellation record is fail-closed at every lifecycle entry point."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from superboss.core.errors import DomainError
    from superboss.modules.files.models import FileUploadLifecycle
    from superboss.modules.files.schemas import UploadStart
    from superboss.modules.files.service import FileLifecycleService, FileService
    from superboss.modules.files.storage import CompletedPart

    project = Project(name="Cancelled lifecycle")
    db_session.add(project)
    await db_session.commit()
    actor = Actor("user", active_owner.id, Role.OWNER, frozenset(), frozenset())
    storage = InMemoryObjectStorage()
    upload = await FileService(db_session, storage).start_upload(
        actor,
        UploadStart(
            project_id=project.id,
            filename="cancelled.pdf",
            category="docs",
            file_date="2026-08-09",
            size_bytes=1,
            sha256="0" * 64,
        ),
        "cancelled",
    )
    lifecycle = await db_session.get(FileUploadLifecycle, upload.id)
    assert lifecycle is not None
    lifecycle.provision_state = "CANCEL_REQUESTED"
    await db_session.commit()
    with pytest.raises(DomainError):
        await FileService(db_session, storage).complete_upload(
            actor, upload.id, [CompletedPart(1, "etag")]
        )
    assert storage.completed == {}
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    assert await FileLifecycleService(factory, storage).reconcile() == 0
    assert storage.completed == {}


@pytest.mark.asyncio
async def test_delete_winner_blocks_later_completion_from_touching_storage(
    db_session, active_owner
) -> None:
    """Once deletion commits, a stale complete request cannot resurrect an object."""
    from superboss.core.errors import NotFoundError
    from superboss.modules.files.models import File
    from superboss.modules.files.schemas import UploadStart
    from superboss.modules.files.service import FileService
    from superboss.modules.files.storage import CompletedPart

    project = Project(name="Delete winner")
    db_session.add(project)
    await db_session.commit()
    actor = Actor("user", active_owner.id, Role.OWNER, frozenset(), frozenset())
    storage = InMemoryObjectStorage()
    upload = await FileService(db_session, storage).start_upload(
        actor,
        UploadStart(
            project_id=project.id,
            filename="winner.pdf",
            category="docs",
            file_date="2026-08-09",
            size_bytes=1,
            sha256="0" * 64,
        ),
        "delete-winner",
    )
    file = await db_session.get(File, upload.file_id)
    assert file is not None
    upload_id = upload.id
    await db_session.delete(file)
    await db_session.commit()
    db_session.expire_all()
    with pytest.raises(NotFoundError):
        await FileService(db_session, storage).complete_upload(
            actor, upload_id, [CompletedPart(1, "etag")]
        )
    assert storage.completed == {} and storage.objects == {}


@pytest.mark.asyncio
async def test_delete_after_completion_enters_storage_leaves_durable_cleanup(
    db_session, active_owner
) -> None:
    """An object completed during a delete race is still covered by the trigger snapshot."""
    import asyncio

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from superboss.core.errors import DomainError
    from superboss.modules.files.models import File, FileStorageCleanup
    from superboss.modules.files.schemas import UploadStart
    from superboss.modules.files.service import FileLifecycleService, FileService
    from superboss.modules.files.storage import CompletedPart, ObjectMetadata

    class BlockingCompleteStorage(InMemoryObjectStorage):
        def __init__(self) -> None:
            super().__init__()
            self.entered = asyncio.Event()
            self.release = asyncio.Event()
            self.calls = 0

        async def complete_multipart(
            self, object_key: str, multipart_id: str, parts: list[CompletedPart]
        ) -> ObjectMetadata:
            self.calls += 1
            self.entered.set()
            await asyncio.wait_for(self.release.wait(), timeout=3)
            return await super().complete_multipart(object_key, multipart_id, parts)

    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    actor = Actor("user", active_owner.id, Role.OWNER, frozenset(), frozenset())
    for round_number in range(3):
        project = Project(name=f"Complete delete race {round_number}")
        db_session.add(project)
        await db_session.commit()
        storage = BlockingCompleteStorage()
        upload = await FileService(db_session, storage).start_upload(
            actor,
            UploadStart(
                project_id=project.id,
                filename="race.pdf",
                category="docs",
                file_date="2026-08-09",
                size_bytes=1,
                sha256="0" * 64,
            ),
            f"complete-delete-{round_number}",
        )
        file_id, upload_id = upload.file_id, upload.id

        async def complete(
            race_storage: BlockingCompleteStorage = storage,
            race_upload_id: UUID = upload_id,
        ) -> None:
            async with factory() as session:
                try:
                    await FileService(session, race_storage).complete_upload(
                        actor, race_upload_id, [CompletedPart(1, "etag")]
                    )
                except DomainError:
                    await session.rollback()

        async def delete_file(race_file_id: UUID = file_id) -> None:
            async with factory() as session:
                file = await session.get(File, race_file_id)
                assert file is not None
                await session.delete(file)
                await session.commit()

        completion = asyncio.create_task(complete())
        await asyncio.wait_for(storage.entered.wait(), timeout=5)
        deletion = asyncio.create_task(delete_file())
        await asyncio.wait_for(asyncio.sleep(0), timeout=1)
        storage.release.set()
        await asyncio.wait_for(asyncio.gather(completion, deletion), timeout=10)
        db_session.expire_all()
        jobs = list(
            await db_session.scalars(
                select(FileStorageCleanup).where(FileStorageCleanup.lifecycle_id == upload_id)
            )
        )
        assert storage.calls <= 1 and len(jobs) == 2
        assert await FileLifecycleService(factory, storage).reconcile() == 2
        assert storage.active == {} and storage.objects == {}


@pytest.mark.asyncio
async def test_live_outbox_lease_allows_one_concurrent_scan_dispatch(
    db_session, active_owner
) -> None:
    """A live claim token prevents a second worker from entering the scan boundary."""
    import asyncio

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from superboss.modules.files.models import FileLifecycleOutbox
    from superboss.modules.files.schemas import UploadStart
    from superboss.modules.files.service import FileLifecycleService, FileService
    from superboss.modules.files.storage import CompletedPart

    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    actor = Actor("user", active_owner.id, Role.OWNER, frozenset(), frozenset())
    for number in range(3):
        project = Project(name=f"Lease dispatch {number}")
        db_session.add(project)
        await db_session.commit()
        storage = InMemoryObjectStorage()
        upload = await FileService(db_session, storage).start_upload(
            actor,
            UploadStart(
                project_id=project.id,
                filename="lease.pdf",
                category="docs",
                file_date="2026-08-09",
                size_bytes=1,
                sha256="0" * 64,
            ),
            f"lease-{number}",
        )
        file_id = upload.file_id
        upload_id = upload.id
        await FileService(db_session, storage).complete_upload(
            actor, upload.id, [CompletedPart(1, "etag")], uuid4()
        )
        audit_job = await db_session.scalar(
            select(FileLifecycleOutbox).where(
                FileLifecycleOutbox.kind == "completion_audit",
                FileLifecycleOutbox.file_id == file_id,
            )
        )
        assert audit_job is not None
        audit_job.state = "DELIVERED"
        await db_session.commit()
        entered = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def dispatch(
            _file_id: UUID,
            _key: UUID,
            race_entered: asyncio.Event = entered,
            race_release: asyncio.Event = release,
        ) -> None:
            nonlocal calls
            calls += 1
            race_entered.set()
            await asyncio.wait_for(race_release.wait(), timeout=3)

        first = asyncio.create_task(
            FileLifecycleService(factory, storage, dispatch).deliver_completion(upload_id)
        )
        await asyncio.wait_for(entered.wait(), timeout=3)
        second = await FileLifecycleService(factory, storage, dispatch).deliver_completion(
            upload_id
        )
        assert not second and calls == 1
        release.set()
        assert await asyncio.wait_for(first, timeout=3)
        db_session.expire_all()
        job = await db_session.scalar(
            select(FileLifecycleOutbox).where(
                FileLifecycleOutbox.kind == "scan_dispatch", FileLifecycleOutbox.file_id == file_id
            )
        )
        assert (
            job is not None
            and job.state == "DELIVERED"
            and job.attempt_count == 1
            and job.claim_token is None
            and job.locked_at is None
        )


@pytest.mark.asyncio
async def test_expired_outbox_lease_takeover_uses_new_token_cas(db_session, active_owner) -> None:
    """An expired owner cannot acknowledge over a newer scan-delivery claim."""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from superboss.modules.files.models import FileLifecycleOutbox
    from superboss.modules.files.schemas import UploadStart
    from superboss.modules.files.service import FileLifecycleService, FileService
    from superboss.modules.files.storage import CompletedPart

    project = Project(name="Lease takeover")
    db_session.add(project)
    await db_session.commit()
    actor = Actor("user", active_owner.id, Role.OWNER, frozenset(), frozenset())
    storage = InMemoryObjectStorage()
    upload = await FileService(db_session, storage).start_upload(
        actor,
        UploadStart(
            project_id=project.id,
            filename="takeover.pdf",
            category="docs",
            file_date="2026-08-09",
            size_bytes=1,
            sha256="0" * 64,
        ),
        "takeover",
    )
    file_id = upload.file_id
    upload_id = upload.id
    await FileService(db_session, storage).complete_upload(
        actor, upload.id, [CompletedPart(1, "etag")], uuid4()
    )
    scan = await db_session.scalar(
        select(FileLifecycleOutbox).where(
            FileLifecycleOutbox.kind == "scan_dispatch", FileLifecycleOutbox.file_id == file_id
        )
    )
    audit = await db_session.scalar(
        select(FileLifecycleOutbox).where(
            FileLifecycleOutbox.kind == "completion_audit", FileLifecycleOutbox.file_id == file_id
        )
    )
    assert scan is not None and audit is not None
    audit.state = "DELIVERED"
    await db_session.commit()
    first_token = uuid4()
    scan.state = "DELIVERING"
    scan.claim_token = first_token
    scan.locked_at = datetime.now(UTC) - timedelta(seconds=31)
    scan.attempt_count = 1
    await db_session.commit()
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    seen: list[UUID] = []
    assert await FileLifecycleService(
        factory, storage, lambda _file, key: seen.append(key)
    ).deliver_completion(upload_id)
    db_session.expire_all()
    scan = await db_session.scalar(
        select(FileLifecycleOutbox).where(
            FileLifecycleOutbox.kind == "scan_dispatch", FileLifecycleOutbox.file_id == file_id
        )
    )
    assert (
        scan is not None
        and scan.state == "DELIVERED"
        and scan.attempt_count == 2
        and scan.claim_token is None
        and scan.locked_at is None
        and seen
    )
    assert first_token != seen[0]


@pytest.mark.asyncio
async def test_future_due_outbox_skips_dispatch_without_claim(db_session, active_owner) -> None:
    """Backoff prevents a worker from claiming or calling a future scan job."""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from superboss.modules.files.models import FileLifecycleOutbox
    from superboss.modules.files.schemas import UploadStart
    from superboss.modules.files.service import FileLifecycleService, FileService
    from superboss.modules.files.storage import CompletedPart

    project = Project(name="Future delivery")
    db_session.add(project)
    await db_session.commit()
    actor = Actor("user", active_owner.id, Role.OWNER, frozenset(), frozenset())
    storage = InMemoryObjectStorage()
    upload = await FileService(db_session, storage).start_upload(
        actor,
        UploadStart(
            project_id=project.id,
            filename="future.pdf",
            category="docs",
            file_date="2026-08-09",
            size_bytes=1,
            sha256="0" * 64,
        ),
        "future",
    )
    file_id = upload.file_id
    await FileService(db_session, storage).complete_upload(
        actor, upload.id, [CompletedPart(1, "etag")], uuid4()
    )
    jobs = list(
        await db_session.scalars(
            select(FileLifecycleOutbox).where(FileLifecycleOutbox.file_id == file_id)
        )
    )
    for job in jobs:
        if job.kind == "completion_audit":
            job.state = "DELIVERED"
        else:
            job.next_attempt_at = datetime.now(UTC) + timedelta(minutes=1)
    await db_session.commit()
    called: list[UUID] = []
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    assert not await FileLifecycleService(
        factory, storage, lambda _file, key: called.append(key)
    ).deliver_completion(upload.id)
    db_session.expire_all()
    scan = await db_session.scalar(
        select(FileLifecycleOutbox).where(
            FileLifecycleOutbox.kind == "scan_dispatch", FileLifecycleOutbox.file_id == file_id
        )
    )
    assert (
        scan is not None
        and scan.state == "PENDING"
        and scan.attempt_count == 0
        and scan.claim_token is None
        and scan.locked_at is None
        and called == []
    )


@pytest.mark.asyncio
async def test_live_cleanup_lease_allows_one_concurrent_external_call(db_session) -> None:
    """A live cleanup lease admits exactly one external delete worker."""
    import asyncio
    import hashlib

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from superboss.modules.files.models import FileStorageCleanup
    from superboss.modules.files.service import FileLifecycleService

    class BlockingDeleteStorage(InMemoryObjectStorage):
        def __init__(self) -> None:
            super().__init__()
            self.entered = asyncio.Event()
            self.release = asyncio.Event()
            self.delete_calls = 0

        async def delete_object(self, object_key: str) -> None:
            self.delete_calls += 1
            self.entered.set()
            await asyncio.wait_for(self.release.wait(), timeout=3)
            await super().delete_object(object_key)

    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    for number in range(3):
        storage = BlockingDeleteStorage()
        object_key = f"projects/lease-cleanup/{number}"
        db_session.add(
            FileStorageCleanup(
                operation="DELETE_OBJECT",
                dedupe_key=hashlib.sha256(object_key.encode()).hexdigest(),
                object_key=object_key,
            )
        )
        await db_session.commit()

        first = asyncio.create_task(
            FileLifecycleService(factory, storage).reconcile_cleanup(limit=1)
        )
        await asyncio.wait_for(storage.entered.wait(), timeout=3)
        second = await FileLifecycleService(factory, storage).reconcile_cleanup(limit=1)
        assert second == 0 and storage.delete_calls == 1
        storage.release.set()
        assert await asyncio.wait_for(first, timeout=3) == 1

        db_session.expire_all()
        cleanup = await db_session.scalar(
            select(FileStorageCleanup).where(FileStorageCleanup.object_key == object_key)
        )
        assert cleanup is not None
        assert cleanup.state == "DONE"
        assert cleanup.attempt_count == 1
        assert cleanup.claim_token is None and cleanup.locked_at is None


@pytest.mark.asyncio
async def test_expired_cleanup_lease_takeover_uses_new_token_cas(db_session) -> None:
    """A late cleanup worker cannot acknowledge over an expired lease takeover."""
    import asyncio
    import hashlib
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select, update
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from superboss.modules.files.models import FileStorageCleanup
    from superboss.modules.files.service import FileLifecycleService

    class TakeoverDeleteStorage(InMemoryObjectStorage):
        def __init__(self) -> None:
            super().__init__()
            self.first_entered = asyncio.Event()
            self.second_entered = asyncio.Event()
            self.release_first = asyncio.Event()
            self.release_second = asyncio.Event()
            self.calls = 0

        async def delete_object(self, object_key: str) -> None:
            self.calls += 1
            if self.calls == 1:
                self.first_entered.set()
                await asyncio.wait_for(self.release_first.wait(), timeout=3)
            else:
                self.second_entered.set()
                await asyncio.wait_for(self.release_second.wait(), timeout=3)
            await super().delete_object(object_key)

    storage = TakeoverDeleteStorage()
    object_key = "projects/lease-cleanup/takeover"
    db_session.add(
        FileStorageCleanup(
            operation="DELETE_OBJECT",
            dedupe_key=hashlib.sha256(object_key.encode()).hexdigest(),
            object_key=object_key,
        )
    )
    await db_session.commit()
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    first = asyncio.create_task(FileLifecycleService(factory, storage).reconcile_cleanup(limit=1))
    await asyncio.wait_for(storage.first_entered.wait(), timeout=3)
    original = await db_session.scalar(
        select(FileStorageCleanup).where(FileStorageCleanup.object_key == object_key)
    )
    assert original is not None and original.claim_token is not None
    cleanup_id = original.id
    old_token = original.claim_token
    await db_session.execute(
        update(FileStorageCleanup)
        .where(FileStorageCleanup.id == cleanup_id)
        .values(locked_at=datetime.now(UTC) - timedelta(seconds=31))
    )
    await db_session.commit()

    second = asyncio.create_task(FileLifecycleService(factory, storage).reconcile_cleanup(limit=1))
    await asyncio.wait_for(storage.second_entered.wait(), timeout=3)
    db_session.expire_all()
    taken_over = await db_session.scalar(
        select(FileStorageCleanup).where(FileStorageCleanup.id == cleanup_id)
    )
    assert taken_over is not None and taken_over.claim_token not in {None, old_token}

    storage.release_first.set()
    assert await asyncio.wait_for(first, timeout=3) == 0
    db_session.expire_all()
    still_owned = await db_session.scalar(
        select(FileStorageCleanup).where(FileStorageCleanup.id == cleanup_id)
    )
    assert still_owned is not None and still_owned.state == "RUNNING"
    assert still_owned.claim_token == taken_over.claim_token

    storage.release_second.set()
    assert await asyncio.wait_for(second, timeout=3) == 1
    db_session.expire_all()
    cleanup = await db_session.scalar(
        select(FileStorageCleanup).where(FileStorageCleanup.id == cleanup_id)
    )
    assert cleanup is not None and cleanup.state == "DONE" and cleanup.attempt_count == 2
    assert cleanup.claim_token is None and cleanup.locked_at is None and storage.calls == 2


@pytest.mark.asyncio
async def test_future_due_cleanup_skips_external_call_without_claim(db_session) -> None:
    """Backoff prevents cleanup workers from claiming future work."""
    import hashlib
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from superboss.modules.files.models import FileStorageCleanup
    from superboss.modules.files.service import FileLifecycleService

    class ObservedStorage(InMemoryObjectStorage):
        def __init__(self) -> None:
            super().__init__()
            self.delete_calls = 0
            self.abort_calls = 0

        async def delete_object(self, object_key: str) -> None:
            self.delete_calls += 1
            await super().delete_object(object_key)

        async def abort_multipart(self, object_key: str, multipart_id: str) -> None:
            self.abort_calls += 1
            await super().abort_multipart(object_key, multipart_id)

    object_key = "projects/lease-cleanup/future"
    db_session.add(
        FileStorageCleanup(
            operation="DELETE_OBJECT",
            dedupe_key=hashlib.sha256(object_key.encode()).hexdigest(),
            object_key=object_key,
            next_attempt_at=datetime.now(UTC) + timedelta(minutes=1),
        )
    )
    await db_session.commit()
    storage = ObservedStorage()
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    assert await FileLifecycleService(factory, storage).reconcile_cleanup(limit=1) == 0
    db_session.expire_all()
    cleanup = await db_session.scalar(
        select(FileStorageCleanup).where(FileStorageCleanup.object_key == object_key)
    )
    assert cleanup is not None and cleanup.state == "PENDING" and cleanup.attempt_count == 0
    assert cleanup.claim_token is None and cleanup.locked_at is None
    assert storage.delete_calls == 0 and storage.abort_calls == 0


@pytest.mark.asyncio
async def test_request_cleanup_and_maintenance_share_one_live_lease(db_session) -> None:
    """Request best-effort cleanup uses the same claim protocol as maintenance."""
    import asyncio
    import hashlib

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from superboss.modules.files.models import FileStorageCleanup
    from superboss.modules.files.service import FileLifecycleService, FileService

    class BlockingDeleteStorage(InMemoryObjectStorage):
        def __init__(self) -> None:
            super().__init__()
            self.entered = asyncio.Event()
            self.release = asyncio.Event()
            self.delete_calls = 0

        async def delete_object(self, object_key: str) -> None:
            self.delete_calls += 1
            self.entered.set()
            await asyncio.wait_for(self.release.wait(), timeout=3)
            await super().delete_object(object_key)

    storage = BlockingDeleteStorage()
    lifecycle_id = uuid4()
    object_key = "projects/lease-cleanup/request-and-maintenance"
    db_session.add(
        FileStorageCleanup(
            operation="DELETE_OBJECT",
            dedupe_key=hashlib.sha256(object_key.encode()).hexdigest(),
            object_key=object_key,
            lifecycle_id=lifecycle_id,
        )
    )
    await db_session.commit()
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    request = asyncio.create_task(
        FileService(db_session, storage)._best_effort_cleanup(lifecycle_id)
    )
    await asyncio.wait_for(storage.entered.wait(), timeout=3)
    maintenance = await FileLifecycleService(factory, storage).reconcile_cleanup(limit=1)
    assert maintenance == 0 and storage.delete_calls == 1
    storage.release.set()
    await asyncio.wait_for(request, timeout=3)

    db_session.expire_all()
    cleanup = await db_session.scalar(
        select(FileStorageCleanup).where(FileStorageCleanup.object_key == object_key)
    )
    assert cleanup is not None and cleanup.state == "DONE" and cleanup.attempt_count == 1
    assert cleanup.claim_token is None and cleanup.locked_at is None


@pytest.mark.asyncio
async def test_reconcile_delivers_failed_scan_without_client_replay(
    db_session, active_owner
) -> None:
    """Maintenance drains a due scan outbox after the original client has gone away."""
    from datetime import UTC, datetime

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from superboss.modules.audit.models import AuditLog
    from superboss.modules.files.models import FileLifecycleOutbox
    from superboss.modules.files.schemas import UploadStart
    from superboss.modules.files.service import FileLifecycleService, FileService
    from superboss.modules.files.storage import CompletedPart

    project = Project(name="Reconcile delivery")
    db_session.add(project)
    await db_session.commit()
    actor = Actor("user", active_owner.id, Role.OWNER, frozenset(), frozenset())
    storage = InMemoryObjectStorage()
    upload = await FileService(db_session, storage).start_upload(
        actor,
        UploadStart(
            project_id=project.id,
            filename="reconcile-delivery.pdf",
            category="docs",
            file_date="2026-08-09",
            size_bytes=1,
            sha256="0" * 64,
        ),
        "reconcile-delivery",
    )
    await FileService(db_session, storage).complete_upload(
        actor, upload.id, [CompletedPart(1, "etag")], uuid4()
    )
    file_id = upload.file_id
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    def fail_dispatch(_file_id: UUID, _delivery_key: UUID) -> None:
        raise RuntimeError("dispatcher secret")

    assert not await FileLifecycleService(factory, storage, fail_dispatch).deliver_completion(
        upload.id
    )
    scan = await db_session.scalar(
        select(FileLifecycleOutbox).where(
            FileLifecycleOutbox.kind == "scan_dispatch",
            FileLifecycleOutbox.file_id == upload.file_id,
        )
    )
    assert scan is not None and scan.state == "PENDING"
    file_id = upload.file_id
    delivery_key = scan.dedupe_key
    scan.next_attempt_at = datetime.now(UTC)
    await db_session.commit()

    delivered: list[tuple[UUID, UUID]] = []
    assert (
        await FileLifecycleService(
            factory, storage, lambda file_id, key: delivered.append((file_id, key))
        ).reconcile()
        == 1
    )
    db_session.expire_all()
    jobs = list(
        await db_session.scalars(
            select(FileLifecycleOutbox)
            .where(FileLifecycleOutbox.file_id == file_id)
            .order_by(FileLifecycleOutbox.kind)
        )
    )
    assert [job.state for job in jobs] == ["DELIVERED", "DELIVERED"]
    assert delivered == [(file_id, delivery_key)]
    assert len(list(await db_session.scalars(select(AuditLog)))) == 1
    assert len(storage.completed) == 1


@pytest.mark.asyncio
async def test_global_delivery_drain_records_audit_before_scan_dispatch(
    db_session, active_owner
) -> None:
    """The maintenance drain delivers the immutable audit before its scan job."""
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from superboss.modules.audit.models import AuditLog
    from superboss.modules.files.models import FileLifecycleOutbox
    from superboss.modules.files.schemas import UploadStart
    from superboss.modules.files.service import FileLifecycleService, FileService
    from superboss.modules.files.storage import CompletedPart

    project = Project(name="Global audit ordering")
    db_session.add(project)
    await db_session.commit()
    actor = Actor("user", active_owner.id, Role.OWNER, frozenset(), frozenset())
    storage = InMemoryObjectStorage()
    upload = await FileService(db_session, storage).start_upload(
        actor,
        UploadStart(
            project_id=project.id,
            filename="global-order.pdf",
            category="docs",
            file_date="2026-08-09",
            size_bytes=1,
            sha256="0" * 64,
        ),
        "global-audit-order",
    )
    await FileService(db_session, storage).complete_upload(
        actor, upload.id, [CompletedPart(1, "etag")], uuid4()
    )
    file_id = upload.file_id
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    delivered: list[tuple[UUID, UUID]] = []

    async def dispatch(file_id: UUID, delivery_key: UUID) -> None:
        async with factory() as session:
            audits = list(await session.scalars(select(AuditLog)))
        assert len(audits) == 1
        delivered.append((file_id, delivery_key))

    assert await FileLifecycleService(factory, storage, dispatch).reconcile() == 1
    db_session.expire_all()
    jobs = list(
        await db_session.scalars(
            select(FileLifecycleOutbox)
            .where(FileLifecycleOutbox.file_id == file_id)
            .order_by(FileLifecycleOutbox.kind)
        )
    )
    assert [job.state for job in jobs] == ["DELIVERED", "DELIVERED"]
    assert delivered == [(file_id, jobs[1].dedupe_key)]


@pytest.mark.asyncio
@pytest.mark.parametrize("live_lease", [False, True])
async def test_global_delivery_drain_skips_future_or_live_scan_lease(
    db_session, active_owner, live_lease: bool
) -> None:
    """Global maintenance never bypasses a future retry time or another live claim."""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from superboss.modules.files.models import FileLifecycleOutbox
    from superboss.modules.files.schemas import UploadStart
    from superboss.modules.files.service import FileLifecycleService, FileService
    from superboss.modules.files.storage import CompletedPart

    project = Project(name=f"Global delivery skip {live_lease}")
    db_session.add(project)
    await db_session.commit()
    actor = Actor("user", active_owner.id, Role.OWNER, frozenset(), frozenset())
    storage = InMemoryObjectStorage()
    upload = await FileService(db_session, storage).start_upload(
        actor,
        UploadStart(
            project_id=project.id,
            filename="global-skip.pdf",
            category="docs",
            file_date="2026-08-09",
            size_bytes=1,
            sha256="0" * 64,
        ),
        f"global-skip-{live_lease}",
    )
    await FileService(db_session, storage).complete_upload(
        actor, upload.id, [CompletedPart(1, "etag")], uuid4()
    )
    file_id = upload.file_id
    jobs = list(
        await db_session.scalars(
            select(FileLifecycleOutbox).where(FileLifecycleOutbox.file_id == file_id)
        )
    )
    for job in jobs:
        if job.kind == "completion_audit":
            job.state = "DELIVERED"
        elif live_lease:
            job.state = "DELIVERING"
            job.claim_token = uuid4()
            job.locked_at = datetime.now(UTC)
            job.attempt_count = 1
        else:
            job.next_attempt_at = datetime.now(UTC) + timedelta(minutes=1)
    await db_session.commit()
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    delivered: list[UUID] = []

    assert (
        await FileLifecycleService(
            factory, storage, lambda _file_id, key: delivered.append(key)
        ).reconcile()
        == 0
    )
    db_session.expire_all()
    scan = await db_session.scalar(
        select(FileLifecycleOutbox).where(
            FileLifecycleOutbox.kind == "scan_dispatch",
            FileLifecycleOutbox.file_id == file_id,
        )
    )
    assert scan is not None and scan.state == ("DELIVERING" if live_lease else "PENDING")
    assert scan.attempt_count == (1 if live_lease else 0) and delivered == []


@pytest.mark.asyncio
async def test_late_complete_timeout_recovers_from_durable_prepared_state(db_session, active_owner) -> None:
    """A timed-out provider call that completes later is recovered by stat, not compensation."""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from superboss.core.errors import FileCompletionPendingError
    from superboss.modules.files.models import (
        File,
        FileLifecycleOutbox,
        FileStorageCleanup,
        FileUploadLifecycle,
    )
    from superboss.modules.files.schemas import UploadStart
    from superboss.modules.files.service import FileLifecycleService, FileService
    from superboss.modules.files.storage import CompletedPart

    project = Project(name="Late completion recovery")
    db_session.add(project)
    await db_session.commit()
    actor = Actor("user", active_owner.id, Role.OWNER, frozenset(), frozenset())
    storage = InMemoryObjectStorage(complete_late_success_delay=0.15)
    service = FileService(db_session, storage)
    upload = await service.start_upload(actor, UploadStart(project_id=project.id, filename="late.pdf", category="docs", file_date="2026-08-09", size_bytes=1, sha256="0" * 64), "late-timeout")
    file_id = upload.file_id
    with pytest.raises(FileCompletionPendingError):
        await service.complete_upload(actor, upload.id, [CompletedPart(1, "etag")])
    file = await db_session.get(File, upload.file_id)
    lifecycle = await db_session.get(FileUploadLifecycle, upload.id)
    assert file is not None and file.state.value == "UPLOADING"
    assert lifecycle is not None and lifecycle.completion_state == "PREPARED" and lifecycle.completion_last_error_code == "COMPLETION_AMBIGUOUS"
    assert list(await db_session.scalars(select(FileStorageCleanup))) == []
    await storage.await_late_completions()
    lifecycle.prepared_at = datetime.now(UTC) - timedelta(seconds=121)
    lifecycle.completion_next_attempt_at = datetime.now(UTC)
    await db_session.commit()
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    assert await FileLifecycleService(factory, storage).reconcile() >= 1
    db_session.expire_all()
    replayed = await db_session.get(File, file_id)
    assert replayed is not None and replayed.state.value == "QUARANTINED" and storage.complete_calls == 1
    assert len(list(await db_session.scalars(select(FileLifecycleOutbox)))) == 2


@pytest.mark.asyncio
async def test_ambiguous_completion_grace_never_retries_complete(db_session, active_owner) -> None:
    """Due replays inside ambiguity grace stat only and retain recovery coordinates."""
    from datetime import UTC, datetime

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from superboss.core.errors import FileCompletionPendingError
    from superboss.modules.files.models import File, FileStorageCleanup, FileUploadLifecycle
    from superboss.modules.files.schemas import UploadStart
    from superboss.modules.files.service import FileLifecycleService, FileService
    from superboss.modules.files.storage import CompletedPart

    project = Project(name="Ambiguous grace")
    db_session.add(project); await db_session.commit()
    actor = Actor("user", active_owner.id, Role.OWNER, frozenset(), frozenset())
    storage = InMemoryObjectStorage(complete_error=TimeoutError("provider secret"))
    service = FileService(db_session, storage)
    upload = await service.start_upload(actor, UploadStart(project_id=project.id, filename="grace.pdf", category="docs", file_date="2026-08-09", size_bytes=1, sha256="0" * 64), "ambiguous-grace")
    parts = [CompletedPart(1, "etag")]
    with pytest.raises(FileCompletionPendingError):
        await service.complete_upload(actor, upload.id, parts)
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    for _ in range(3):
        lifecycle = await db_session.get(FileUploadLifecycle, upload.id)
        assert lifecycle is not None
        lifecycle.completion_next_attempt_at = datetime.now(UTC)
        await db_session.commit()
        assert await FileLifecycleService(factory, storage).reconcile() == 0
        assert storage.complete_calls == 1
    file = await db_session.get(File, upload.file_id)
    lifecycle = await db_session.get(FileUploadLifecycle, upload.id)
    assert storage.complete_calls == 1 and file is not None and file.state.value == "UPLOADING"
    assert lifecycle is not None and lifecycle.completion_state == "PREPARED"
    assert lifecycle.completion_last_error_code == "COMPLETION_AMBIGUOUS" and "secret" not in lifecycle.completion_last_error_code
    assert list(await db_session.scalars(select(FileStorageCleanup))) == []


@pytest.mark.asyncio
async def test_reconcile_prepared_wrong_size_compensates_object(db_session, active_owner) -> None:
    """A recovered PREPARED object with the wrong size is durably compensated."""
    import hashlib
    import json
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from superboss.modules.files.models import File, FileStorageCleanup, FileUploadLifecycle
    from superboss.modules.files.schemas import UploadStart
    from superboss.modules.files.service import FileLifecycleService, FileService
    from superboss.modules.files.storage import ObjectMetadata

    project = Project(name="Prepared wrong size")
    db_session.add(project); await db_session.commit()
    actor = Actor("user", active_owner.id, Role.OWNER, frozenset(), frozenset())
    storage = InMemoryObjectStorage(complete_size=2)
    upload = await FileService(db_session, storage).start_upload(actor, UploadStart(project_id=project.id, filename="wrong.pdf", category="docs", file_date="2026-08-09", size_bytes=1, sha256="0" * 64), "prepared-wrong")
    upload_id = upload.id
    file_id = upload.file_id
    lifecycle = await db_session.get(FileUploadLifecycle, upload_id)
    assert lifecycle is not None
    parts = [{"part_number": 1, "etag": "etag"}]
    lifecycle.completion_state = "PREPARED"
    lifecycle.canonical_parts_json = parts
    lifecycle.parts_digest = hashlib.sha256(json.dumps(parts, separators=(",", ":")).encode()).hexdigest()
    lifecycle.completion_actor_kind = "user"
    lifecycle.completion_actor_id = active_owner.id
    lifecycle.completion_actor_role = "OWNER"
    lifecycle.completion_request_id = uuid4()
    lifecycle.completion_event_key = uuid4()
    lifecycle.prepared_at = datetime.now(UTC) - timedelta(seconds=121)
    storage.objects[lifecycle.object_key] = ObjectMetadata(2)
    await db_session.commit()
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    assert await FileLifecycleService(factory, storage).reconcile() >= 1
    db_session.expire_all()
    file = await db_session.get(File, file_id)
    lifecycle = await db_session.get(FileUploadLifecycle, upload_id)
    jobs = list(await db_session.scalars(select(FileStorageCleanup)))
    assert file is not None and file.state.value == "FAILED"
    assert lifecycle.completion_state == "COMPENSATION_PENDING" and {job.operation for job in jobs} == {"DELETE_OBJECT", "ABORT_MULTIPART"}


@pytest.mark.asyncio
async def test_reconcile_skips_inflight_completion_attempt(db_session, active_owner) -> None:
    """A durable future completion window prevents maintenance from duplicating S3 complete."""
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from superboss.modules.files.schemas import UploadStart
    from superboss.modules.files.service import FileLifecycleService, FileService
    from superboss.modules.files.storage import CompletedPart, ObjectMetadata

    class BlockingStorage(InMemoryObjectStorage):
        def __init__(self) -> None:
            super().__init__()
            self.entered = asyncio.Event(); self.release = asyncio.Event()

        async def complete_multipart(self, object_key: str, multipart_id: str, parts: list[CompletedPart]) -> ObjectMetadata:
            self.complete_calls += 1; self.entered.set()
            await asyncio.wait_for(self.release.wait(), timeout=3)
            self.active.pop(multipart_id)
            self.completed[multipart_id] = parts
            metadata = ObjectMetadata(self.complete_size)
            self.objects[object_key] = metadata
            return metadata

    project = Project(name="Inflight reconcile")
    db_session.add(project); await db_session.commit()
    actor = Actor("user", active_owner.id, Role.OWNER, frozenset(), frozenset())
    storage = BlockingStorage()
    upload = await FileService(db_session, storage).start_upload(actor, UploadStart(project_id=project.id, filename="inflight.pdf", category="docs", file_date="2026-08-09", size_bytes=1, sha256="0" * 64), "inflight")
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    request = asyncio.create_task(FileService(db_session, storage).complete_upload(actor, upload.id, [CompletedPart(1, "etag")]))
    await asyncio.wait_for(storage.entered.wait(), timeout=3)
    assert await FileLifecycleService(factory, storage).reconcile() == 0 and storage.complete_calls == 1
    storage.release.set(); result = await asyncio.wait_for(request, timeout=3)
    assert result.state.value == "QUARANTINED" and storage.complete_calls == 1


@pytest.mark.asyncio
async def test_delete_during_ambiguous_completion_defers_cleanup_until_grace_expires(
    db_session, active_owner
) -> None:
    """Deleting a PREPARED upload must not race a provider's late completion."""
    from datetime import UTC, datetime

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from superboss.core.errors import FileCompletionPendingError
    from superboss.modules.files.models import File, FileStorageCleanup
    from superboss.modules.files.schemas import UploadStart
    from superboss.modules.files.service import FileLifecycleService, FileService
    from superboss.modules.files.storage import CompletedPart

    project = Project(name="Delete during completion ambiguity")
    db_session.add(project)
    await db_session.commit()
    actor = Actor("user", active_owner.id, Role.OWNER, frozenset(), frozenset())
    storage = InMemoryObjectStorage(complete_late_success_delay=0.15)
    service = FileService(db_session, storage)
    upload = await service.start_upload(
        actor,
        UploadStart(
            project_id=project.id,
            filename="late-delete.pdf",
            category="docs",
            file_date="2026-08-09",
            size_bytes=1,
            sha256="0" * 64,
        ),
        "late-delete",
    )
    file_id, upload_id = upload.file_id, upload.id
    with pytest.raises(FileCompletionPendingError):
        await service.complete_upload(actor, upload_id, [CompletedPart(1, "etag")])

    file = await db_session.get(File, file_id)
    assert file is not None
    object_key = file.object_key
    await db_session.delete(file)
    await db_session.commit()
    db_session.expire_all()
    jobs = list(
        await db_session.scalars(
            select(FileStorageCleanup).where(FileStorageCleanup.lifecycle_id == upload_id)
        )
    )
    assert {job.operation for job in jobs} == {"DELETE_OBJECT", "ABORT_MULTIPART"}
    now = datetime.now(UTC)
    assert all(job.next_attempt_at > now for job in jobs)

    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    assert await FileLifecycleService(factory, storage).reconcile() == 0
    assert storage.aborted == set() and storage.deleted == []
    await storage.await_late_completions()
    assert object_key in storage.objects

    for job in jobs:
        job.next_attempt_at = datetime.now(UTC)
    await db_session.commit()
    assert await FileLifecycleService(factory, storage).reconcile() == 2
    db_session.expire_all()
    finished = list(
        await db_session.scalars(
            select(FileStorageCleanup).where(FileStorageCleanup.lifecycle_id == upload_id)
        )
    )
    assert all(job.state == "DONE" for job in finished)
    assert storage.objects == {} and storage.active == {} and storage.aborted


@pytest.mark.asyncio
async def test_cleanup_batch_claims_all_rows_before_any_external_work(db_session) -> None:
    """A second worker cannot claim a later row from a live claimed batch."""
    import asyncio
    import hashlib
    from collections import Counter

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from superboss.modules.files.models import FileStorageCleanup
    from superboss.modules.files.service import FileLifecycleService

    class BatchBarrierStorage(InMemoryObjectStorage):
        def __init__(self) -> None:
            super().__init__()
            self.calls: list[str] = []
            self.first_entered = asyncio.Event()
            self.second_entered = asyncio.Event()
            self.release_first = asyncio.Event()
            self.release_second = asyncio.Event()

        async def delete_object(self, object_key: str) -> None:
            self.calls.append(object_key)
            if object_key.endswith("/first"):
                self.first_entered.set()
                await asyncio.wait_for(self.release_first.wait(), timeout=3)
            else:
                if self.calls.count(object_key) == 1:
                    self.second_entered.set()
                await asyncio.wait_for(self.release_second.wait(), timeout=3)
            await super().delete_object(object_key)

    keys = ["projects/batch-lease/first", "projects/batch-lease/second"]
    for key in keys:
        db_session.add(
            FileStorageCleanup(
                operation="DELETE_OBJECT",
                dedupe_key=hashlib.sha256(key.encode()).hexdigest(),
                object_key=key,
            )
        )
    await db_session.commit()
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    storage = BatchBarrierStorage()

    first_worker = asyncio.create_task(
        FileLifecycleService(factory, storage).reconcile_cleanup(limit=2)
    )
    await asyncio.wait_for(storage.first_entered.wait(), timeout=3)
    second_worker = asyncio.create_task(
        FileLifecycleService(factory, storage).reconcile_cleanup(limit=2)
    )
    assert await asyncio.wait_for(second_worker, timeout=3) == 0
    assert storage.calls == [keys[0]]
    storage.release_first.set()
    await asyncio.wait_for(storage.second_entered.wait(), timeout=3)
    storage.release_second.set()
    assert await asyncio.wait_for(first_worker, timeout=3) == 2

    db_session.expire_all()
    jobs = list(
        await db_session.scalars(
            select(FileStorageCleanup).where(FileStorageCleanup.object_key.in_(keys))
        )
    )
    assert Counter(storage.calls) == Counter({key: 1 for key in keys})
    assert all(job.state == "DONE" and job.attempt_count == 1 for job in jobs)


@pytest.mark.asyncio
async def test_delete_before_prepared_commit_cannot_complete_after_cleanup(db_session, active_owner) -> None:
    """A delete observing NONE cannot finish cleanup before a request completes storage."""
    import asyncio
    from datetime import UTC, datetime

    from sqlalchemy import select, text
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from superboss.core.errors import DomainError
    from superboss.modules.files.models import File, FileStorageCleanup
    from superboss.modules.files.schemas import UploadStart
    from superboss.modules.files.service import FileLifecycleService, FileService
    from superboss.modules.files.storage import CompletedPart

    prepared_flushed = asyncio.Event()
    prepared_committed = asyncio.Event()
    allow_prepared_commit = asyncio.Event()
    allow_request_continue = asyncio.Event()

    class ControlledPrepareSession(AsyncSession):
        async def commit(self) -> None:
            if not prepared_flushed.is_set():
                await self.flush()
                prepared_flushed.set()
                await asyncio.wait_for(allow_prepared_commit.wait(), timeout=5)
                await super().commit()
                prepared_committed.set()
                await asyncio.wait_for(allow_request_continue.wait(), timeout=5)
                return
            await super().commit()

    base_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    request_factory = async_sessionmaker(
        db_session.bind, class_=ControlledPrepareSession, expire_on_commit=False
    )
    project = Project(name="Delete before prepared commit")
    db_session.add(project)
    await db_session.commit()
    actor = Actor("user", active_owner.id, Role.OWNER, frozenset(), frozenset())
    storage = InMemoryObjectStorage()
    upload = await FileService(db_session, storage).start_upload(
        actor,
        UploadStart(
            project_id=project.id,
            filename="commit-race.pdf",
            category="docs",
            file_date="2026-08-09",
            size_bytes=1,
            sha256="0" * 64,
        ),
        "delete-before-prepared",
    )
    file_id, upload_id = upload.file_id, upload.id
    request_session = request_factory()
    delete_started = asyncio.Event()
    delete_pid: list[int] = []

    async def delete_file() -> None:
        async with base_factory() as delete_session:
            delete_pid.append(int(await delete_session.scalar(text("SELECT pg_backend_pid()"))))
            delete_started.set()
            file = await delete_session.get(File, file_id)
            assert file is not None
            await delete_session.delete(file)
            await delete_session.commit()

    request = asyncio.create_task(
        FileService(request_session, storage).complete_upload(
            actor, upload_id, [CompletedPart(1, "etag")]
        )
    )
    deletion: asyncio.Task[None] | None = None
    try:
        await asyncio.wait_for(prepared_flushed.wait(), timeout=5)
        deletion = asyncio.create_task(delete_file())
        await asyncio.wait_for(delete_started.wait(), timeout=5)
        for _ in range(300):
            wait_event = await db_session.scalar(
                text("SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid"),
                {"pid": delete_pid[0]},
            )
            if wait_event == "Lock":
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("delete did not reach the lifecycle lock")

        allow_prepared_commit.set()
        await asyncio.wait_for(prepared_committed.wait(), timeout=5)
        await asyncio.wait_for(deletion, timeout=5)
        db_session.expire_all()
        jobs = list(
            await db_session.scalars(
                select(FileStorageCleanup).where(FileStorageCleanup.lifecycle_id == upload_id)
            )
        )
        assert len(jobs) == 2
        assert await FileLifecycleService(base_factory, storage).reconcile_cleanup() == 0
        db_session.expire_all()
        jobs = list(
            await db_session.scalars(
                select(FileStorageCleanup).where(FileStorageCleanup.lifecycle_id == upload_id)
            )
        )
        assert all(job.state == "PENDING" and job.next_attempt_at > datetime.now(UTC) for job in jobs)

        allow_request_continue.set()
        with pytest.raises(DomainError):
            await asyncio.wait_for(request, timeout=5)
        assert storage.complete_calls == 0 and storage.objects == {}

        for job in jobs:
            job.next_attempt_at = datetime.now(UTC)
        await db_session.commit()
        assert await FileLifecycleService(base_factory, storage).reconcile_cleanup() == 2
        db_session.expire_all()
        jobs = list(
            await db_session.scalars(
                select(FileStorageCleanup).where(FileStorageCleanup.lifecycle_id == upload_id)
            )
        )
        assert all(job.state == "DONE" for job in jobs)
    finally:
        allow_prepared_commit.set()
        allow_request_continue.set()
        if deletion is not None and not deletion.done():
            await asyncio.wait_for(deletion, timeout=5)
        if not request.done():
            try:
                await asyncio.wait_for(request, timeout=5)
            except DomainError:
                pass
        await request_session.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("delete_target", ["file", "project"])
async def test_due_prepared_replay_and_delete_share_a_safe_lock_order(
    db_session, active_owner, delete_target: str
) -> None:
    """A due replay must not deadlock with raw File or Project deletion."""
    import asyncio
    import hashlib
    import json
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select, text
    from sqlalchemy.exc import DBAPIError
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from superboss.modules.files.models import File, FileStorageCleanup, FileUploadLifecycle
    from superboss.modules.files.schemas import UploadStart
    from superboss.modules.files.service import FileService
    from superboss.modules.files.storage import CompletedPart

    entered_fresh_context = asyncio.Event()
    release_replay = asyncio.Event()

    class BarrierFileService(FileService):
        async def _fresh_active_completion_context(self, upload_id):
            entered_fresh_context.set()
            await asyncio.wait_for(release_replay.wait(), timeout=5)
            return await super()._fresh_active_completion_context(upload_id)

    project = Project(name=f"Due replay delete {delete_target}")
    db_session.add(project)
    await db_session.commit()
    actor = Actor("user", active_owner.id, Role.OWNER, frozenset(), frozenset())
    storage = InMemoryObjectStorage()
    parts = [CompletedPart(1, "etag")]
    upload = await FileService(db_session, storage).start_upload(
        actor,
        UploadStart(
            project_id=project.id,
            filename="due-replay.pdf",
            category="docs",
            file_date="2026-08-09",
            size_bytes=1,
            sha256="0" * 64,
        ),
        f"due-replay-{delete_target}",
    )
    file_id, upload_id = upload.file_id, upload.id
    lifecycle = await db_session.get(FileUploadLifecycle, upload_id)
    assert lifecycle is not None
    canonical = [{"part_number": 1, "etag": "etag"}]
    lifecycle.completion_state = "PREPARED"
    lifecycle.parts_digest = hashlib.sha256(
        json.dumps(canonical, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    lifecycle.canonical_parts_json = canonical
    lifecycle.completion_event_key = uuid4()
    lifecycle.prepared_at = datetime.now(UTC) - timedelta(seconds=121)
    lifecycle.completion_attempt_count = 1
    lifecycle.completion_next_attempt_at = datetime.now(UTC)
    await db_session.commit()

    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    delete_started = asyncio.Event()
    delete_pid: list[int] = []

    async def replay() -> Exception | None:
        async with factory() as session:
            try:
                await BarrierFileService(session, storage).complete_upload(actor, upload_id, parts)
            except Exception as error:  # noqa: BLE001 -- test captures database outcome
                await session.rollback()
                return error
        return None

    async def delete() -> Exception | None:
        async with factory() as session:
            delete_pid.append(int(await session.scalar(text("SELECT pg_backend_pid()"))))
            delete_started.set()
            try:
                if delete_target == "file":
                    target = await session.get(File, file_id)
                else:
                    target = await session.get(Project, project.id)
                assert target is not None
                await session.delete(target)
                await session.commit()
            except Exception as error:  # noqa: BLE001 -- test captures database outcome
                await session.rollback()
                return error
        return None

    replay_task = asyncio.create_task(replay())
    delete_task: asyncio.Task[Exception | None] | None = None
    try:
        await asyncio.wait_for(entered_fresh_context.wait(), timeout=5)
        delete_task = asyncio.create_task(delete())
        await asyncio.wait_for(delete_started.wait(), timeout=5)
        for _ in range(300):
            wait_event = await db_session.scalar(
                text("SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid"),
                {"pid": delete_pid[0]},
            )
            if wait_event == "Lock":
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("delete did not wait for the lifecycle lock")
        release_replay.set()
        replay_error, delete_error = await asyncio.wait_for(
            asyncio.gather(replay_task, delete_task), timeout=10
        )
        assert not any(isinstance(error, DBAPIError) for error in (replay_error, delete_error))

        db_session.expire_all()
        file = await db_session.get(File, file_id)
        jobs = list(
            await db_session.scalars(
                select(FileStorageCleanup).where(FileStorageCleanup.lifecycle_id == upload_id)
            )
        )
        assert file is not None or jobs
        assert storage.complete_calls <= 1
    finally:
        release_replay.set()
        if delete_task is not None and not delete_task.done():
            await asyncio.wait_for(delete_task, timeout=10)
        if not replay_task.done():
            await asyncio.wait_for(replay_task, timeout=10)


@pytest.mark.asyncio
@pytest.mark.parametrize("delete_target", ["file", "project"])
async def test_reconcile_due_prepared_and_delete_share_a_safe_lock_order(
    db_session, active_owner, delete_target: str
) -> None:
    """PREPARED maintenance uses the same File-before-lifecycle ordering as replay."""
    import asyncio
    import hashlib
    import json
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select, text
    from sqlalchemy.exc import DBAPIError
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from superboss.modules.files.models import File, FileStorageCleanup, FileUploadLifecycle
    from superboss.modules.files.schemas import UploadStart
    from superboss.modules.files.service import FileLifecycleService, FileService
    from superboss.modules.files.storage import ObjectMetadata

    class BlockingStatStorage(InMemoryObjectStorage):
        def __init__(self) -> None:
            super().__init__()
            self.entered = asyncio.Event()
            self.release = asyncio.Event()

        async def stat_object(self, object_key: str) -> ObjectMetadata | None:
            self.entered.set()
            await asyncio.wait_for(self.release.wait(), timeout=5)
            return await super().stat_object(object_key)

    project = Project(name=f"Maintenance replay delete {delete_target}")
    db_session.add(project)
    await db_session.commit()
    actor = Actor("user", active_owner.id, Role.OWNER, frozenset(), frozenset())
    storage = BlockingStatStorage()
    upload = await FileService(db_session, storage).start_upload(
        actor,
        UploadStart(
            project_id=project.id,
            filename="maintenance-replay.pdf",
            category="docs",
            file_date="2026-08-09",
            size_bytes=1,
            sha256="0" * 64,
        ),
        f"maintenance-replay-{delete_target}",
    )
    file_id, upload_id = upload.file_id, upload.id
    lifecycle = await db_session.get(FileUploadLifecycle, upload_id)
    assert lifecycle is not None
    canonical = [{"part_number": 1, "etag": "etag"}]
    lifecycle.completion_state = "PREPARED"
    lifecycle.parts_digest = hashlib.sha256(
        json.dumps(canonical, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    lifecycle.canonical_parts_json = canonical
    lifecycle.completion_event_key = uuid4()
    lifecycle.prepared_at = datetime.now(UTC) - timedelta(seconds=121)
    lifecycle.completion_attempt_count = 1
    lifecycle.completion_next_attempt_at = datetime.now(UTC)
    await db_session.commit()

    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    delete_started = asyncio.Event()
    delete_pid: list[int] = []

    async def reconcile() -> Exception | None:
        try:
            await FileLifecycleService(factory, storage).reconcile()
        except Exception as error:  # noqa: BLE001 -- test captures database outcome
            return error
        return None

    async def delete() -> Exception | None:
        async with factory() as session:
            delete_pid.append(int(await session.scalar(text("SELECT pg_backend_pid()"))))
            delete_started.set()
            try:
                target = (
                    await session.get(File, file_id)
                    if delete_target == "file"
                    else await session.get(Project, project.id)
                )
                assert target is not None
                await session.delete(target)
                await session.commit()
            except Exception as error:  # noqa: BLE001 -- test captures database outcome
                await session.rollback()
                return error
        return None

    reconcile_task = asyncio.create_task(reconcile())
    delete_task: asyncio.Task[Exception | None] | None = None
    try:
        await asyncio.wait_for(storage.entered.wait(), timeout=5)
        delete_task = asyncio.create_task(delete())
        await asyncio.wait_for(delete_started.wait(), timeout=5)
        for _ in range(300):
            wait_event = await db_session.scalar(
                text("SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid"),
                {"pid": delete_pid[0]},
            )
            if wait_event == "Lock":
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("delete did not wait for the File lock")
        storage.release.set()
        reconcile_error, delete_error = await asyncio.wait_for(
            asyncio.gather(reconcile_task, delete_task), timeout=10
        )
        assert not any(isinstance(error, DBAPIError) for error in (reconcile_error, delete_error))
        db_session.expire_all()
        file = await db_session.get(File, file_id)
        jobs = list(
            await db_session.scalars(
                select(FileStorageCleanup).where(FileStorageCleanup.lifecycle_id == upload_id)
            )
        )
        assert file is not None or jobs
        assert storage.complete_calls <= 1
    finally:
        storage.release.set()
        if delete_task is not None and not delete_task.done():
            await asyncio.wait_for(delete_task, timeout=10)
        if not reconcile_task.done():
            await asyncio.wait_for(reconcile_task, timeout=10)


@pytest.mark.asyncio
@pytest.mark.parametrize("delete_target", ["file", "project"])
async def test_provisioning_create_and_delete_leave_abortable_snapshot(
    db_session, active_owner, delete_target: str
) -> None:
    """A delete racing a provider-created multipart must retain abort coordinates."""
    import asyncio
    from datetime import UTC, datetime

    from sqlalchemy import select, text
    from sqlalchemy.exc import DBAPIError
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from superboss.modules.files.models import File, FileStorageCleanup, FileUploadLifecycle, Upload
    from superboss.modules.files.schemas import UploadStart
    from superboss.modules.files.service import FileLifecycleService, FileService

    class BlockingCreateStorage(InMemoryObjectStorage):
        def __init__(self) -> None:
            super().__init__()
            self.created = asyncio.Event()
            self.release = asyncio.Event()

        async def create_multipart(self, object_key: str, content_type: str) -> str:
            multipart_id = await super().create_multipart(object_key, content_type)
            self.created.set()
            await asyncio.wait_for(self.release.wait(), timeout=5)
            return multipart_id

    project = Project(name=f"Provision delete {delete_target}")
    db_session.add(project)
    await db_session.commit()
    actor = Actor("user", active_owner.id, Role.OWNER, frozenset(), frozenset())
    storage = BlockingCreateStorage()
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    idempotency_key = f"provision-delete-{delete_target}"
    command = UploadStart(
        project_id=project.id,
        filename="provisioning.pdf",
        category="docs",
        file_date="2026-08-09",
        size_bytes=1,
        sha256="0" * 64,
    )
    delete_started = asyncio.Event()
    delete_pid: list[int] = []

    async def provision() -> Exception | None:
        async with factory() as session:
            try:
                await FileService(session, storage).start_upload(actor, command, idempotency_key)
            except Exception as error:  # noqa: BLE001 -- the race may safely reject provisioning
                await session.rollback()
                return error
        return None

    provision_task = asyncio.create_task(provision())
    delete_task: asyncio.Task[Exception | None] | None = None
    try:
        await asyncio.wait_for(storage.created.wait(), timeout=5)
        upload = await db_session.scalar(
            select(Upload).where(Upload.idempotency_key == idempotency_key)
        )
        assert upload is not None
        file_id, upload_id = upload.file_id, upload.id
        lifecycle = await db_session.get(FileUploadLifecycle, upload_id)
        assert lifecycle is not None
        assert lifecycle.provision_state == "PROVISIONING" and lifecycle.multipart_id is None

        async def delete() -> Exception | None:
            async with factory() as session:
                delete_pid.append(int(await session.scalar(text("SELECT pg_backend_pid()"))))
                delete_started.set()
                try:
                    target = (
                        await session.get(File, file_id)
                        if delete_target == "file"
                        else await session.get(Project, project.id)
                    )
                    assert target is not None
                    await session.delete(target)
                    await session.commit()
                except Exception as error:  # noqa: BLE001 -- test observes database outcome
                    await session.rollback()
                    return error
            return None

        delete_task = asyncio.create_task(delete())
        await asyncio.wait_for(delete_started.wait(), timeout=5)
        for _ in range(300):
            wait_event = await db_session.scalar(
                text("SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid"),
                {"pid": delete_pid[0]},
            )
            if wait_event == "Lock":
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("delete did not wait for the provisioning lifecycle lock")

        storage.release.set()
        provision_error, delete_error = await asyncio.wait_for(
            asyncio.gather(provision_task, delete_task), timeout=10
        )
        assert not any(isinstance(error, DBAPIError) for error in (provision_error, delete_error))

        db_session.expire_all()
        jobs = list(
            await db_session.scalars(
                select(FileStorageCleanup).where(FileStorageCleanup.lifecycle_id == upload_id)
            )
        )
        operations = {job.operation for job in jobs}
        assert operations == {"DELETE_OBJECT", "ABORT_MULTIPART"}, {
            "operations": sorted(operations),
            "active_multipart_ids": sorted(storage.active),
            "provision_outcome": type(provision_error).__name__ if provision_error else "returned",
            "delete_outcome": type(delete_error).__name__ if delete_error else "returned",
        }
        for job in jobs:
            job.next_attempt_at = datetime.now(UTC)
        await db_session.commit()
        assert await FileLifecycleService(factory, storage).reconcile_cleanup() == 2
        assert storage.active == {}
    finally:
        storage.release.set()
        if delete_task is not None and not delete_task.done():
            await asyncio.wait_for(delete_task, timeout=10)
        if not provision_task.done():
            await asyncio.wait_for(provision_task, timeout=10)


@pytest.mark.asyncio
@pytest.mark.parametrize("delete_target", ["file", "project"])
@pytest.mark.parametrize("scenario", ["bind_commit_failure", "response_loss_list_failure"])
async def test_failed_provisioning_delete_retains_recoverable_multipart_discovery(
    db_session, active_owner, delete_target: str, scenario: str
) -> None:
    """Provider IDs created before a rollback survive deletion as executable cleanup."""
    import asyncio
    from datetime import UTC, datetime

    from sqlalchemy import select, text
    from sqlalchemy.exc import DBAPIError
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from superboss.core.errors import FileProvisioningPendingError
    from superboss.modules.files.models import File, FileStorageCleanup, FileUploadLifecycle, Upload
    from superboss.modules.files.schemas import UploadStart
    from superboss.modules.files.service import FileLifecycleService, FileService

    class FailureStorage(InMemoryObjectStorage):
        def __init__(self) -> None:
            super().__init__()
            self.provider_created = asyncio.Event()
            self.replay_listing = asyncio.Event()
            self.release = asyncio.Event()
            self.fail_replay_listing = True

        async def create_multipart(self, object_key: str, content_type: str) -> str:
            multipart_id = await super().create_multipart(object_key, content_type)
            self.provider_created.set()
            if scenario == "bind_commit_failure":
                await asyncio.wait_for(self.release.wait(), timeout=5)
                return multipart_id
            raise RuntimeError("provider response lost")

        async def list_multipart_uploads(self, object_key: str) -> list[str]:
            if (
                scenario == "response_loss_list_failure"
                and self.list_calls >= 1
                and self.fail_replay_listing
            ):
                self.list_calls += 1
                self.replay_listing.set()
                await asyncio.wait_for(self.release.wait(), timeout=5)
                raise RuntimeError("temporary list failure")
            return await super().list_multipart_uploads(object_key)

    class FailBindCommitSession(AsyncSession):
        async def commit(self) -> None:
            commits = getattr(self, "commits", 0) + 1
            self.commits = commits
            if scenario == "bind_commit_failure" and commits == 2:
                raise RuntimeError("bind commit failure")
            await super().commit()

    project = Project(name=f"Provision rollback {scenario} {delete_target}")
    db_session.add(project)
    await db_session.commit()
    actor = Actor("user", active_owner.id, Role.OWNER, frozenset(), frozenset())
    storage = FailureStorage()
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    failing_factory = async_sessionmaker(
        db_session.bind, class_=FailBindCommitSession, expire_on_commit=False
    )
    idempotency_key = f"provision-rollback-{scenario}-{delete_target}"
    command = UploadStart(
        project_id=project.id,
        filename="rollback.pdf",
        category="docs",
        file_date="2026-08-09",
        size_bytes=1,
        sha256="0" * 64,
    )
    delete_started = asyncio.Event()
    delete_pid: list[int] = []

    async def start(session_factory) -> Exception | None:
        async with session_factory() as session:
            try:
                await FileService(session, storage).start_upload(actor, command, idempotency_key)
            except Exception as error:  # noqa: BLE001 -- test captures the service boundary outcome
                await session.rollback()
                return error
        return None

    if scenario == "response_loss_list_failure":
        initial_error = await start(factory)
        assert isinstance(initial_error, FileProvisioningPendingError)
        provision_task = asyncio.create_task(start(factory))
        await asyncio.wait_for(storage.replay_listing.wait(), timeout=5)
    else:
        provision_task = asyncio.create_task(start(failing_factory))
        await asyncio.wait_for(storage.provider_created.wait(), timeout=5)

    delete_task: asyncio.Task[Exception | None] | None = None
    try:
        upload = await db_session.scalar(
            select(Upload).where(Upload.idempotency_key == idempotency_key)
        )
        assert upload is not None
        file_id, upload_id = upload.file_id, upload.id
        lifecycle = await db_session.get(FileUploadLifecycle, upload_id)
        assert lifecycle is not None
        assert lifecycle.provision_state == "PROVISIONING" and lifecycle.multipart_id is None

        async def delete() -> Exception | None:
            async with factory() as session:
                delete_pid.append(int(await session.scalar(text("SELECT pg_backend_pid()"))))
                delete_started.set()
                try:
                    target = (
                        await session.get(File, file_id)
                        if delete_target == "file"
                        else await session.get(Project, project.id)
                    )
                    assert target is not None
                    await session.delete(target)
                    await session.commit()
                except Exception as error:  # noqa: BLE001 -- test captures the database outcome
                    await session.rollback()
                    return error
            return None

        delete_task = asyncio.create_task(delete())
        await asyncio.wait_for(delete_started.wait(), timeout=5)
        for _ in range(300):
            wait_event = await db_session.scalar(
                text("SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid"),
                {"pid": delete_pid[0]},
            )
            if wait_event == "Lock":
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("delete did not wait for the provisioning lock")

        storage.release.set()
        provision_error, delete_error = await asyncio.wait_for(
            asyncio.gather(provision_task, delete_task), timeout=10
        )
        assert isinstance(provision_error, FileProvisioningPendingError)
        assert not any(isinstance(error, DBAPIError) for error in (provision_error, delete_error))

        db_session.expire_all()
        jobs = list(
            await db_session.scalars(
                select(FileStorageCleanup).where(FileStorageCleanup.lifecycle_id == upload_id)
            )
        )
        for job in jobs:
            job.next_attempt_at = datetime.now(UTC)
        await db_session.commit()
        storage.fail_replay_listing = False
        await FileLifecycleService(factory, storage).reconcile_cleanup()
        assert storage.active == {}, {
            "scenario": scenario,
            "target": delete_target,
            "operations": sorted(job.operation for job in jobs),
            "active_multipart_ids": sorted(storage.active),
            "aborted_ids": sorted(storage.aborted),
        }
    finally:
        storage.release.set()
        if delete_task is not None and not delete_task.done():
            await asyncio.wait_for(delete_task, timeout=10)
        if not provision_task.done():
            await asyncio.wait_for(provision_task, timeout=10)


@pytest.mark.asyncio
@pytest.mark.parametrize("delete_target", ["file", "project"])
async def test_multiple_discovered_multipart_ids_survive_cleanup_commit_failure_and_delete(
    db_session, active_owner, delete_target: str
) -> None:
    """A failed multi-ID cleanup commit cannot detach any active multipart from recovery."""
    import asyncio
    from datetime import UTC, datetime

    from sqlalchemy import select, text
    from sqlalchemy.exc import DBAPIError
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from superboss.modules.files.models import File, FileStorageCleanup, Upload
    from superboss.modules.files.schemas import UploadStart
    from superboss.modules.files.service import FileLifecycleService, FileService

    cleanup_commit_started = asyncio.Event()
    release_cleanup_failure = asyncio.Event()

    class MultipleActiveStorage(InMemoryObjectStorage):
        async def list_multipart_uploads(self, object_key: str) -> list[str]:
            self.list_calls += 1
            self.active = {"multipart-a": object_key, "multipart-b": object_key}
            return ["multipart-a", "multipart-b"]

    class FailCleanupCommitSession(AsyncSession):
        async def commit(self) -> None:
            commits = getattr(self, "commits", 0) + 1
            self.commits = commits
            if commits == 2:
                cleanup_commit_started.set()
                await asyncio.wait_for(release_cleanup_failure.wait(), timeout=5)
                raise RuntimeError("cleanup commit failure")
            await super().commit()

    project = Project(name=f"Multi cleanup rollback {delete_target}")
    db_session.add(project)
    await db_session.commit()
    actor = Actor("user", active_owner.id, Role.OWNER, frozenset(), frozenset())
    storage = MultipleActiveStorage()
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    failing_factory = async_sessionmaker(
        db_session.bind, class_=FailCleanupCommitSession, expire_on_commit=False
    )
    key = f"multi-cleanup-{delete_target}"
    command = UploadStart(
        project_id=project.id,
        filename="multiple.pdf",
        category="docs",
        file_date="2026-08-09",
        size_bytes=1,
        sha256="0" * 64,
    )
    delete_pid: list[int] = []
    delete_started = asyncio.Event()

    async def provision() -> Exception | None:
        async with failing_factory() as session:
            try:
                await FileService(session, storage).start_upload(actor, command, key)
            except Exception as error:  # noqa: BLE001 -- test captures safe provider outcome
                await session.rollback()
                return error
        return None

    provision_task = asyncio.create_task(provision())
    delete_task: asyncio.Task[Exception | None] | None = None
    try:
        await asyncio.wait_for(cleanup_commit_started.wait(), timeout=5)
        upload = await db_session.scalar(select(Upload).where(Upload.idempotency_key == key))
        assert upload is not None
        file_id, upload_id = upload.file_id, upload.id

        async def delete() -> Exception | None:
            async with factory() as session:
                delete_pid.append(int(await session.scalar(text("SELECT pg_backend_pid()"))))
                delete_started.set()
                try:
                    target = (
                        await session.get(File, file_id)
                        if delete_target == "file"
                        else await session.get(Project, project.id)
                    )
                    assert target is not None
                    await session.delete(target)
                    await session.commit()
                except Exception as error:  # noqa: BLE001 -- test captures the database outcome
                    await session.rollback()
                    return error
            return None

        delete_task = asyncio.create_task(delete())
        await asyncio.wait_for(delete_started.wait(), timeout=5)
        for _ in range(300):
            wait_event = await db_session.scalar(
                text("SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid"),
                {"pid": delete_pid[0]},
            )
            if wait_event == "Lock":
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("delete did not wait for failed multi-ID cleanup")

        release_cleanup_failure.set()
        provision_error, delete_error = await asyncio.wait_for(
            asyncio.gather(provision_task, delete_task), timeout=10
        )
        assert not any(isinstance(error, DBAPIError) for error in (provision_error, delete_error))
        db_session.expire_all()
        jobs = list(
            await db_session.scalars(
                select(FileStorageCleanup).where(FileStorageCleanup.lifecycle_id == upload_id)
            )
        )
        for job in jobs:
            job.next_attempt_at = datetime.now(UTC)
        await db_session.commit()
        await FileLifecycleService(factory, storage).reconcile_cleanup()
        assert storage.active == {}, {
            "target": delete_target,
            "operations": sorted(job.operation for job in jobs),
            "active_multipart_ids": sorted(storage.active),
            "aborted_ids": sorted(storage.aborted),
        }
    finally:
        release_cleanup_failure.set()
        if delete_task is not None and not delete_task.done():
            await asyncio.wait_for(delete_task, timeout=10)
        if not provision_task.done():
            await asyncio.wait_for(provision_task, timeout=10)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["list", "abort"])
async def test_discovery_cleanup_retries_without_persisting_provider_errors(
    db_session, failure: str
) -> None:
    """A discovery lease stays retryable until every exact-key multipart is aborted."""
    import hashlib
    from datetime import UTC, datetime

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from superboss.modules.files.models import FileStorageCleanup
    from superboss.modules.files.service import FileLifecycleService

    class FlakyDiscoveryStorage(InMemoryObjectStorage):
        def __init__(self) -> None:
            super().__init__()
            self.abort_calls: list[str] = []
            self.fail_abort = failure == "abort"

        async def abort_multipart(self, object_key: str, multipart_id: str) -> None:
            self.abort_calls.append(multipart_id)
            if self.fail_abort:
                self.fail_abort = False
                raise RuntimeError("provider abort secret")
            await super().abort_multipart(object_key, multipart_id)

    storage = FlakyDiscoveryStorage()
    object_key = f"projects/discovery/{failure}"
    storage.active = {"discover-a": object_key, "discover-b": object_key}
    if failure == "list":
        storage.list_error = RuntimeError("provider list secret")
    cleanup = FileStorageCleanup(
        operation="DISCOVER_MULTIPART",
        dedupe_key=hashlib.sha256(
            f"DISCOVER_MULTIPART\x1f{object_key}\x1f".encode()
        ).hexdigest(),
        object_key=object_key,
    )
    db_session.add(cleanup)
    await db_session.commit()
    cleanup_id = cleanup.id
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    service = FileLifecycleService(factory, storage)

    assert await service.reconcile_cleanup() == 0
    db_session.expire_all()
    persisted = await db_session.get(FileStorageCleanup, cleanup_id)
    assert persisted is not None
    assert persisted.state == "PENDING" and persisted.last_error_code == "DISCOVERY_FAILED"
    assert "secret" not in str(persisted.last_error_code).lower()

    storage.list_error = None
    persisted.next_attempt_at = datetime.now(UTC)
    await db_session.commit()
    assert await service.reconcile_cleanup() == 1
    assert storage.active == {} and storage.aborted == {"discover-a", "discover-b"}
    db_session.expire_all()
    persisted = await db_session.get(FileStorageCleanup, cleanup_id)
    assert persisted is not None and persisted.state == "DONE"


@pytest.mark.asyncio
async def test_maintenance_recovers_unbound_multipart_after_bind_commit_failure(
    db_session, active_owner
) -> None:
    """A durable PROVISIONING row recovers without requiring a client replay."""
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from superboss.core.errors import FileProvisioningPendingError
    from superboss.modules.files.models import FileUploadLifecycle, Upload
    from superboss.modules.files.schemas import UploadStart
    from superboss.modules.files.service import FileLifecycleService, FileService

    class FailBindCommitSession(AsyncSession):
        async def commit(self) -> None:
            commits = getattr(self, "commits", 0) + 1
            self.commits = commits
            if commits == 2:
                raise RuntimeError("bind commit secret")
            await super().commit()

    project = Project(name="Maintenance provision recovery")
    db_session.add(project)
    await db_session.commit()
    actor = Actor("user", active_owner.id, Role.OWNER, frozenset(), frozenset())
    storage = InMemoryObjectStorage()
    failing_factory = async_sessionmaker(
        db_session.bind, class_=FailBindCommitSession, expire_on_commit=False
    )
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    command = UploadStart(
        project_id=project.id,
        filename="maintenance.pdf",
        category="docs",
        file_date="2026-08-09",
        size_bytes=1,
        sha256="0" * 64,
    )
    async with failing_factory() as session:
        with pytest.raises(FileProvisioningPendingError):
            await FileService(session, storage).start_upload(actor, command, "maintenance-bind")
    upload = await db_session.scalar(select(Upload).where(Upload.idempotency_key == "maintenance-bind"))
    assert upload is not None
    upload_id = upload.id
    assert await FileLifecycleService(factory, storage).reconcile() >= 1
    db_session.expire_all()
    lifecycle = await db_session.get(FileUploadLifecycle, upload_id)
    upload = await db_session.get(Upload, upload_id)
    assert lifecycle is not None and upload is not None
    assert lifecycle.provision_state == "READY" and upload.multipart_id in storage.active
    assert storage.create_calls == 1


@pytest.mark.asyncio
async def test_abort_ack_loss_retries_missing_multipart_to_done(db_session) -> None:
    """A lost DONE commit must retry a missing provider multipart as an idempotent abort."""
    import hashlib
    from datetime import UTC, datetime, timedelta

    from botocore.exceptions import ClientError
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from superboss.infrastructure.s3 import Boto3ObjectStorage
    from superboss.modules.files.models import FileStorageCleanup
    from superboss.modules.files.service import FileLifecycleService

    class StrictAbortClient:
        def __init__(self) -> None:
            self.active = {"ack-loss-upload"}
            self.calls = 0
            self.successful_aborts = 0

        def abort_multipart_upload(self, **_kwargs: object) -> dict[str, object]:
            self.calls += 1
            if "ack-loss-upload" in self.active:
                self.active.remove("ack-loss-upload")
                self.successful_aborts += 1
                return {}
            raise ClientError(
                {"Error": {"Code": "NoSuchUpload"}}, "AbortMultipartUpload"
            )

    class FailAckCommitSession(AsyncSession):
        async def commit(self) -> None:
            commits = getattr(self, "commits", 0) + 1
            self.commits = commits
            if commits == 2:
                raise RuntimeError("ack commit failure")
            await super().commit()

    object_key = "projects/ack-loss/file.pdf"
    cleanup = FileStorageCleanup(
        operation="ABORT_MULTIPART",
        dedupe_key=hashlib.sha256(
            f"ABORT_MULTIPART\x1f{object_key}\x1fack-loss-upload".encode()
        ).hexdigest(),
        object_key=object_key,
        multipart_id="ack-loss-upload",
    )
    db_session.add(cleanup)
    await db_session.commit()
    cleanup_id = cleanup.id
    client = StrictAbortClient()
    storage = Boto3ObjectStorage(bucket="files-bucket", client=client)  # type: ignore[arg-type]
    failing_factory = async_sessionmaker(
        db_session.bind, class_=FailAckCommitSession, expire_on_commit=False
    )
    normal_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    with pytest.raises(RuntimeError, match="ack commit failure"):
        await FileLifecycleService(failing_factory, storage).reconcile_cleanup()
    db_session.expire_all()
    persisted = await db_session.get(FileStorageCleanup, cleanup_id)
    assert persisted is not None
    assert persisted.state == "RUNNING" and persisted.attempt_count == 1
    assert persisted.claim_token is not None and client.active == set()

    persisted.locked_at = datetime.now(UTC) - timedelta(seconds=31)
    await db_session.commit()
    assert await FileLifecycleService(normal_factory, storage).reconcile_cleanup() == 1
    db_session.expire_all()
    persisted = await db_session.get(FileStorageCleanup, cleanup_id)
    assert persisted is not None
    assert (
        persisted.state == "DONE"
        and persisted.attempt_count == 2
        and persisted.claim_token is None
        and persisted.locked_at is None
    )
    assert client.active == set() and client.successful_aborts == 1 and client.calls == 2
