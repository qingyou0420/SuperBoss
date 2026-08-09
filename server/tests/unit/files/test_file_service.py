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
    assert storage.expiries == [300, 300]


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
    assert storage.expiries == [300]


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
    assert storage.expiries == [300]


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
