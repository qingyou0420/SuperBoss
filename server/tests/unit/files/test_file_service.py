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
    assert await storage.presign_upload_part("projects/x/a", upload, 1, 300) == f"memory://part/{upload}/1"
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
    file = File(id=uuid4(), project_id=uuid4(), filename="report.pdf", category="资料", object_key="x", size_bytes=1, sha256="0" * 64, state=FileState.QUARANTINED, uploader_id=uuid4())
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
    command = UploadStart(project_id=project.id, filename="x.pdf", size_bytes=1, sha256="0" * 64, category="资料", file_date="2026-08-09")
    service = FileService(db_session, storage)
    first = await service.start_upload(actor, command, "same")
    second = await service.start_upload(actor, command, "same")
    assert first.id == second.id
    assert len(storage.active) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    {"filename": "y.pdf"}, {"category": "合同"}, {"file_date": date(2026, 8, 10)},
    {"size_bytes": 2}, {"sha256": "1" * 64}, {"content_type": "image/png"},
])
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
    original = UploadStart(project_id=project.id, filename="x.pdf", size_bytes=1, sha256="0" * 64, category="资料", file_date="2026-08-09", content_type="application/pdf")
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
    staff = User(wecom_userid="file-staff", display_name="Staff", role=Role.STAFF, status=UserStatus.ACTIVE)
    db_session.add_all([first_project, second_project, staff])
    await db_session.flush()
    db_session.add(ProjectMember(project_id=second_project.id, user_id=staff.id))
    storage = InMemoryObjectStorage()
    service = FileService(db_session, storage)
    owner = Actor("user", active_owner.id, Role.OWNER, frozenset(), frozenset())
    staff_actor = Actor("user", staff.id, Role.STAFF, frozenset({second_project.id}), frozenset())
    def command(project_id): return UploadStart(project_id=project_id, filename="x.pdf", size_bytes=1, sha256="0" * 64, category="资料", file_date="2026-08-09")
    first = await service.start_upload(owner, command(first_project.id), "same")
    second = await service.start_upload(owner, command(second_project.id), "same")
    third = await service.start_upload(staff_actor, command(second_project.id), "same")
    assert len({first.id, second.id, third.id}) == 3 and len(storage.active) == 3


@pytest.mark.asyncio
async def test_complete_sorts_parts_and_quarantines_without_enqueue(db_session, active_owner) -> None:
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
    upload = await service.start_upload(actor, UploadStart(project_id=project.id, filename="x.pdf", size_bytes=2, sha256="0" * 64, category="资料", file_date="2026-08-09"), "complete")
    file = await service.complete_upload(actor, upload.id, [CompletedPart(2, "not-a-sha"), CompletedPart(1, "0" * 64)])
    assert file.state.value == "QUARANTINED" and file.sha256 == "0" * 64
    assert storage.completed[upload.multipart_id] == [CompletedPart(1, "0" * 64), CompletedPart(2, "not-a-sha")]
    assert enqueued == []


@pytest.mark.asyncio
async def test_size_mismatch_aborts_and_persists_failed(db_session, active_owner) -> None:
    from superboss.core.errors import FileUploadSizeMismatchError
    from superboss.modules.files.schemas import UploadStart
    from superboss.modules.files.service import FileService
    from superboss.modules.files.storage import CompletedPart
    project = Project(name="Mismatch"); db_session.add(project); await db_session.flush()
    actor = Actor("user", active_owner.id, Role.OWNER, frozenset(), frozenset())
    storage = InMemoryObjectStorage(complete_size=2); service = FileService(db_session, storage)
    upload = await service.start_upload(actor, UploadStart(project_id=project.id, filename="x.pdf", size_bytes=1, sha256="0" * 64, category="资料", file_date="2026-08-09"), "mismatch")
    with pytest.raises(FileUploadSizeMismatchError): await service.complete_upload(actor, upload.id, [CompletedPart(1, "e")])
    await db_session.refresh(await db_session.get(__import__("superboss.modules.files.models", fromlist=["File"]).File, upload.file_id))
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
    upload = await service.start_upload(actor, UploadStart(project_id=project.id, filename="x.pdf", size_bytes=1, sha256="0" * 64, category="资料", file_date="2026-08-09"), "parts")
    assert (await service.presign_part(actor, upload.id, 1)).endswith("/1")
    assert (await service.presign_part(actor, upload.id, 10_000)).endswith("/10000")
    assert storage.expiries == [900, 900]


@pytest.mark.asyncio
async def test_part_missing_upload_fails_closed(db_session, active_owner) -> None:
    from superboss.core.errors import NotFoundError
    from superboss.modules.files.service import FileService
    actor = Actor("user", active_owner.id, Role.OWNER, frozenset(), frozenset())
    with pytest.raises(NotFoundError): await FileService(db_session, InMemoryObjectStorage()).presign_part(actor, uuid4(), 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["QUARANTINED", "SCANNING", "CLEAN", "INFECTED", "FAILED"])
async def test_part_rejects_every_non_uploading_state(db_session, active_owner, state) -> None:
    from superboss.core.errors import ConflictError
    from superboss.modules.files.models import FileState
    from superboss.modules.files.schemas import UploadStart
    from superboss.modules.files.service import FileService
    project = Project(name=f"Part {state}"); db_session.add(project); await db_session.flush()
    actor = Actor("user", active_owner.id, Role.OWNER, frozenset(), frozenset())
    service = FileService(db_session, InMemoryObjectStorage())
    upload = await service.start_upload(actor, UploadStart(project_id=project.id, filename="x.pdf", size_bytes=1, sha256="0" * 64, category="资料", file_date="2026-08-09"), f"{state}-key")
    file = await db_session.get(__import__("superboss.modules.files.models", fromlist=["File"]).File, upload.file_id)
    assert file is not None; file.state = FileState(state); await db_session.flush()
    with pytest.raises(ConflictError): await service.presign_part(actor, upload.id, 1)


@pytest.mark.asyncio
async def test_foreign_staff_cannot_presign_part(db_session, active_owner) -> None:
    from superboss.core.errors import ForbiddenError
    from superboss.modules.files.schemas import UploadStart
    from superboss.modules.files.service import FileService
    target, assigned = Project(name="Target part"), Project(name="Assigned part")
    staff = User(wecom_userid="part-staff", display_name="Staff", role=Role.STAFF, status=UserStatus.ACTIVE)
    db_session.add_all([target, assigned, staff]); await db_session.flush()
    db_session.add(ProjectMember(project_id=assigned.id, user_id=staff.id))
    storage = InMemoryObjectStorage(); service = FileService(db_session, storage)
    owner = Actor("user", active_owner.id, Role.OWNER, frozenset(), frozenset())
    upload = await service.start_upload(owner, UploadStart(project_id=target.id, filename="x.pdf", size_bytes=1, sha256="0" * 64, category="资料", file_date="2026-08-09"), "foreign")
    staff_actor = Actor("user", staff.id, Role.STAFF, frozenset({assigned.id}), frozenset())
    with pytest.raises(ForbiddenError): await service.presign_part(staff_actor, upload.id, 1)
    assert storage.expiries == []


@pytest.mark.parametrize("key", ["x", "!" * 255, "", "x" * 256, " x", "x ", "x\r\ny", "中文"])
def test_idempotency_key_grammar(key: str) -> None:
    """Header keys are printable ASCII tokens, never whitespace or controls."""
    import re
    assert bool(re.fullmatch(r"[!-~]{1,255}", key)) == (key in {"x", "!" * 255})


@pytest.mark.asyncio
@pytest.mark.parametrize("abort_error", [None, RuntimeError("abort secret")])
async def test_storage_error_is_safe_and_leaves_failed_file(db_session, active_owner, abort_error) -> None:
    from superboss.core.errors import FileStorageFailureError
    from superboss.modules.files.schemas import UploadStart
    from superboss.modules.files.service import FileService
    from superboss.modules.files.storage import CompletedPart
    project = Project(name="Storage error"); db_session.add(project); await db_session.flush()
    storage = InMemoryObjectStorage(complete_error=RuntimeError("S3 secret"), abort_error=abort_error)
    actor = Actor("user", active_owner.id, Role.OWNER, frozenset(), frozenset()); service = FileService(db_session, storage)
    upload = await service.start_upload(actor, UploadStart(project_id=project.id, filename="x.pdf", size_bytes=1, sha256="0" * 64, category="资料", file_date="2026-08-09"), "storage-error")
    with pytest.raises(FileStorageFailureError) as error: await service.complete_upload(actor, upload.id, [CompletedPart(1, "e")])
    assert "secret" not in str(error.value).lower()
    assert upload.multipart_id in storage.aborted


@pytest.mark.asyncio
async def test_deleted_file_cascades_upload_and_operations_fail_closed(db_session, active_owner) -> None:
    from superboss.core.errors import NotFoundError
    from superboss.modules.files.models import File
    from superboss.modules.files.schemas import UploadStart
    from superboss.modules.files.service import FileService
    from superboss.modules.files.storage import CompletedPart
    project = Project(name="Cascade"); db_session.add(project); await db_session.flush()
    storage = InMemoryObjectStorage(); actor = Actor("user", active_owner.id, Role.OWNER, frozenset(), frozenset()); service = FileService(db_session, storage)
    upload = await service.start_upload(actor, UploadStart(project_id=project.id, filename="x.pdf", size_bytes=1, sha256="0" * 64, category="资料", file_date="2026-08-09"), "cascade")
    upload_id = upload.id
    file = await db_session.get(File, upload.file_id); assert file is not None
    await db_session.delete(file); await db_session.commit(); db_session.expire_all()
    with pytest.raises(NotFoundError): await service.presign_part(actor, upload_id, 1)
    with pytest.raises(NotFoundError): await service.complete_upload(actor, upload_id, [CompletedPart(1, "e")])
    assert storage.expiries == [] and storage.completed == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["UPLOADING", "QUARANTINED", "SCANNING", "INFECTED", "FAILED"])
async def test_download_rejects_non_clean_state(db_session, active_owner, state) -> None:
    from superboss.modules.files.models import File, FileState
    from superboss.modules.files.service import FileNotReadyError, FileService
    project = Project(name=f"Download {state}"); db_session.add(project); await db_session.flush()
    file = File(project_id=project.id, filename="secret.pdf", category="资料", file_date=date(2026, 8, 9), object_key="projects/x/secret", size_bytes=1, sha256="0" * 64, uploader_id=active_owner.id, uploader_kind="user", content_type="application/pdf", state=FileState(state))
    db_session.add(file); await db_session.flush()
    storage = InMemoryObjectStorage(); actor = Actor("user", active_owner.id, Role.OWNER, frozenset(), frozenset())
    with pytest.raises(FileNotReadyError): await FileService(db_session, storage).presign_download(actor, file.id)
    assert storage.expiries == []


@pytest.mark.asyncio
async def test_clean_download_owner_returns_key_url_with_short_expiry(db_session, active_owner) -> None:
    from superboss.modules.files.models import File, FileState
    from superboss.modules.files.service import FileService
    project = Project(name="Clean download"); db_session.add(project); await db_session.flush()
    file = File(project_id=project.id, filename="x.pdf", category="资料", file_date=date(2026, 8, 9), object_key="projects/clean/key", size_bytes=1, sha256="0" * 64, uploader_id=active_owner.id, uploader_kind="user", content_type="application/pdf", state=FileState.CLEAN)
    db_session.add(file); await db_session.flush(); storage = InMemoryObjectStorage()
    actor = Actor("user", active_owner.id, Role.OWNER, frozenset(), frozenset())
    assert await FileService(db_session, storage).presign_download(actor, file.id) == "memory://get/projects/clean/key"
    assert storage.expiries == [60]


@pytest.mark.asyncio
async def test_clean_download_assigned_staff_and_foreign_denial(db_session, active_owner) -> None:
    from superboss.core.errors import ForbiddenError
    from superboss.modules.files.models import File, FileState
    from superboss.modules.files.service import FileService
    project, other = Project(name="Staff download"), Project(name="Other download")
    staff = User(wecom_userid="download-staff", display_name="Staff", role=Role.STAFF, status=UserStatus.ACTIVE)
    db_session.add_all([project, other, staff]); await db_session.flush(); db_session.add(ProjectMember(project_id=project.id, user_id=staff.id))
    file = File(project_id=project.id, filename="x.pdf", category="资料", file_date=date(2026, 8, 9), object_key="projects/staff/key", size_bytes=1, sha256="0" * 64, uploader_id=active_owner.id, uploader_kind="user", content_type="application/pdf", state=FileState.CLEAN)
    db_session.add(file); await db_session.flush(); storage = InMemoryObjectStorage(); service = FileService(db_session, storage)
    assigned = Actor("user", staff.id, Role.STAFF, frozenset({project.id}), frozenset())
    assert await service.presign_download(assigned, file.id) == "memory://get/projects/staff/key"
    foreign = Actor("user", staff.id, Role.STAFF, frozenset({other.id}), frozenset())
    with pytest.raises(ForbiddenError): await service.presign_download(foreign, file.id)
    assert storage.expiries == [60]


@pytest.mark.asyncio
async def test_missing_download_file_fails_closed(db_session, active_owner) -> None:
    from superboss.core.errors import NotFoundError
    from superboss.modules.files.service import FileService
    storage = InMemoryObjectStorage(); actor = Actor("user", active_owner.id, Role.OWNER, frozenset(), frozenset())
    with pytest.raises(NotFoundError): await FileService(db_session, storage).presign_download(actor, uuid4())
    assert storage.expiries == []


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,role", [("device", None), ("system", None), ("device", Role.OWNER), ("system", Role.STAFF), ("user", None)])
async def test_download_rejects_unsupported_actor_shapes(db_session, active_owner, kind, role) -> None:
    from superboss.core.errors import ForbiddenError
    from superboss.modules.files.models import File, FileState
    from superboss.modules.files.service import FileService
    project = Project(name=f"Actor {kind} {role}"); db_session.add(project); await db_session.flush()
    file = File(project_id=project.id, filename="x.pdf", category="资料", file_date=date(2026, 8, 9), object_key="projects/actor/key", size_bytes=1, sha256="0" * 64, uploader_id=active_owner.id, uploader_kind="user", content_type="application/pdf", state=FileState.CLEAN)
    db_session.add(file); await db_session.flush(); storage = InMemoryObjectStorage()
    with pytest.raises(ForbiddenError): await FileService(db_session, storage).presign_download(Actor(kind, active_owner.id, role, frozenset(), frozenset()), file.id)
    assert storage.expiries == []


@pytest.mark.asyncio
@pytest.mark.parametrize("round_number", range(3))
async def test_concurrent_same_metadata_reuses_winner_without_loser_multipart(db_session, active_owner, round_number: int) -> None:
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
    command = UploadStart(project_id=project.id, filename="x.pdf", size_bytes=1, sha256="0" * 64, category="资料", file_date="2026-08-09")
    storage = InMemoryObjectStorage()
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    barrier = asyncio.Barrier(2)
    async def create() -> tuple[UUID, UUID]:
        async with factory() as session:
            await barrier.wait()
            upload = await FileService(session, storage).start_upload(actor, command, f"race-{round_number}")
            await session.commit()
            return upload.id, upload.file_id
    first, second = await asyncio.wait_for(asyncio.gather(create(), create()), timeout=10)
    assert first == second and len(storage.active) == 1 and storage.create_calls == 1 and storage.aborted == set()
    assert await db_session.scalar(select(func.count()).select_from(Upload)) == 1
    assert await db_session.scalar(select(func.count()).select_from(File)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("round_number", range(3))
async def test_concurrent_different_metadata_conflicts_without_loser_multipart(db_session, active_owner, round_number: int) -> None:
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
    first = UploadStart(project_id=project.id, filename="x.pdf", size_bytes=1, sha256="0" * 64, category="资料", file_date="2026-08-09", content_type="application/pdf")
    second = first.model_copy(update={"content_type": "image/png"})
    storage = InMemoryObjectStorage()
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    barrier = asyncio.Barrier(2)
    async def create(command: UploadStart):
        async with factory() as session:
            try:
                await barrier.wait()
                upload = await FileService(session, storage).start_upload(actor, command, f"race-conflict-{round_number}")
                await session.commit()
                return upload
            except ConflictError:
                return None
    results = await asyncio.wait_for(asyncio.gather(create(first), create(second)), timeout=10)
    winner = next(item for item in results if item is not None)
    assert sum(item is not None for item in results) == 1 and len(storage.active) == 1 and storage.create_calls == 1 and storage.aborted == set()
    saved = await db_session.get(Upload, winner.id)
    assert saved is not None and await db_session.scalar(select(func.count()).select_from(Upload)) == 1


@pytest.mark.asyncio
async def test_empty_multipart_response_leaves_safe_durable_provisioning(db_session, active_owner) -> None:
    from sqlalchemy import select

    from superboss.core.errors import DomainError
    from superboss.modules.files.models import FileUploadLifecycle, Upload
    from superboss.modules.files.schemas import UploadStart
    from superboss.modules.files.service import FileService
    project = Project(name="Empty multipart response")
    db_session.add(project); await db_session.flush()
    storage = InMemoryObjectStorage(created_multipart_id="")
    actor = Actor("user", active_owner.id, Role.OWNER, frozenset(), frozenset())
    with pytest.raises(DomainError) as error:
        await FileService(db_session, storage).start_upload(actor, UploadStart(project_id=project.id, filename="x.pdf", size_bytes=1, sha256="0" * 64, category="资料", file_date="2026-08-09"), "constraint")
    assert error.value.code == "FILE_PROVISIONING_PENDING" and "secret" not in str(error.value).lower()
    upload = await db_session.scalar(select(Upload))
    lifecycle = await db_session.scalar(select(FileUploadLifecycle))
    assert upload is not None and lifecycle is not None and upload.multipart_id is None
    assert lifecycle.provision_state == "PROVISIONING" and "" in storage.active


@pytest.mark.asyncio
async def test_concurrent_complete_locks_upload_before_storage_completion(db_session, active_owner) -> None:
    """Without a row lock both sessions pass the state check and complete the same upload."""
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker

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
                except FileUploadNotActiveError:
                    await session.rollback()
                    return False

        results = await asyncio.wait_for(asyncio.gather(complete_once(), complete_once()), timeout=5)
        db_session.expire_all()
        file = await db_session.get(File, file_id)
        assert results == [True, True]
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
        UploadStart(project_id=project.id, filename="x.pdf", size_bytes=1, sha256="0" * 64, category="docs", file_date="2026-08-09"),
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
    command = UploadStart(project_id=project.id, filename="x.pdf", size_bytes=1, sha256="0" * 64, category="docs", file_date="2026-08-09")
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
    command = UploadStart(project_id=project.id, filename="x.pdf", size_bytes=1, sha256="0" * 64, category="docs", file_date="2026-08-09")

    with pytest.raises(DomainError) as pending:
        await FileService(db_session, MultipleActiveStorage()).start_upload(actor, command, "duplicates")
    assert pending.value.code == "FILE_PROVISIONING_PENDING"
    lifecycle = await db_session.scalar(select(FileUploadLifecycle))
    upload = await db_session.scalar(select(Upload))
    cleanup = list((await db_session.scalars(select(FileStorageCleanup))).all())
    assert lifecycle is not None and upload is not None and lifecycle.provision_state == "PROVISIONING"
    assert upload.multipart_id is None
    assert {item.multipart_id for item in cleanup} == {"multipart-a", "multipart-b"}
    assert all(item.operation == "ABORT_MULTIPART" and item.last_error_code is None for item in cleanup)


@pytest.mark.asyncio
async def test_start_replay_recovers_after_exact_key_listing_failure(db_session, active_owner) -> None:
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
    command = UploadStart(project_id=project.id, filename="x.pdf", size_bytes=1, sha256="0" * 64, category="docs", file_date="2026-08-09")
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
async def test_completion_replay_finalizes_once_and_persists_safe_outbox(db_session, active_owner) -> None:
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
    outbox = list(await db_session.scalars(select(FileLifecycleOutbox).order_by(FileLifecycleOutbox.kind)))
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
        "id", "kind", "dedupe_key", "file_id", "project_id", "state", "attempt_count",
        "next_attempt_at", "locked_at", "last_error_code", "created_at", "updated_at",
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
    cleanup = list(await db_session.scalars(select(FileStorageCleanup).order_by(FileStorageCleanup.operation)))
    assert file is not None and file.state == FileState.FAILED
    assert lifecycle is not None and lifecycle.completion_state == "COMPENSATION_PENDING"
    assert [entry.operation for entry in cleanup] == ["ABORT_MULTIPART", "DELETE_OBJECT"]
    assert cleanup[1].state == "PENDING" and cleanup[1].last_error_code == "DELETE_FAILED"
    assert "secret" not in str(cleanup[1].last_error_code)
    assert await storage.stat_object(file.object_key) is not None
    object_key = file.object_key

    storage.delete_error = None
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    assert await FileLifecycleService(factory, storage).reconcile() == 1
    db_session.expire_all()
    cleanup = list(await db_session.scalars(select(FileStorageCleanup).order_by(FileStorageCleanup.operation)))
    assert [entry.state for entry in cleanup] == ["DONE", "DONE"]
    assert await storage.stat_object(object_key) is None


@pytest.mark.asyncio
async def test_reconciler_finalizes_durable_prepared_object_without_second_complete(
    db_session, active_owner
) -> None:
    """A crash after provider completion is recoverable without a client replay."""
    from datetime import UTC, datetime

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
    lifecycle.prepared_at = datetime.now(UTC)
    await db_session.commit()
    await storage.complete_multipart(lifecycle.object_key, upload.multipart_id, [CompletedPart(1, "etag")])
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

    assert not await FileLifecycleService(factory, storage, fail_dispatch).deliver_completion(upload_id)
    db_session.expire_all()
    jobs = list(await db_session.scalars(select(FileLifecycleOutbox).order_by(FileLifecycleOutbox.kind)))
    audits = list(await db_session.scalars(select(AuditLog)))
    assert [job.state for job in jobs] == ["DELIVERED", "PENDING"]
    assert jobs[1].last_error_code == "DISPATCH_FAILED"
    assert len(audits) == 1 and "secret" not in str(jobs[1].last_error_code)

    seen: list[tuple[UUID, UUID]] = []
    assert await FileLifecycleService(factory, storage, lambda file_id, key: seen.append((file_id, key))).deliver_completion(upload_id)
    db_session.expire_all()
    jobs = list(await db_session.scalars(select(FileLifecycleOutbox).order_by(FileLifecycleOutbox.kind)))
    assert [job.state for job in jobs] == ["DELIVERED", "DELIVERED"]
    assert seen == [(file_id, jobs[1].dedupe_key)]
    assert len(list(await db_session.scalars(select(AuditLog)))) == 1


@pytest.mark.asyncio
async def test_database_file_delete_snapshots_cleanup_before_cascade(db_session, active_owner) -> None:
    """The database trigger preserves storage coordinates after ORM file deletion."""
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

    jobs = list(await db_session.scalars(select(FileStorageCleanup).order_by(FileStorageCleanup.operation)))
    assert [job.operation for job in jobs] == ["ABORT_MULTIPART", "DELETE_OBJECT"]
    assert all(job.state == "PENDING" and job.object_key == object_key for job in jobs)
    assert await db_session.get(Upload, upload_id) is None
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    assert await FileLifecycleService(factory, storage).reconcile() == 2
    db_session.expire_all()
    jobs = list(await db_session.scalars(select(FileStorageCleanup).order_by(FileStorageCleanup.operation)))
    assert [job.state for job in jobs] == ["DONE", "DONE"]
    assert storage.active == {}
