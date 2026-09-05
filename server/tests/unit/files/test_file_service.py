"""File upload state-machine and drive folder behavior."""

from uuid import UUID, uuid4

import pytest

from superboss.core.actors import Actor
from superboss.modules.files.models import File, FileState, FolderVisibility
from superboss.modules.users.models import Role
from tests.files.factory import add_folder, make_file
from tests.files.storage import InMemoryObjectStorage
from tests.identity import local_user


def _start(folder_id: UUID, **overrides: object):
    from superboss.modules.files.schemas import UploadStart

    payload: dict[str, object] = {
        "folder_id": folder_id,
        "filename": "x.pdf",
        "size_bytes": 1,
        "sha256": "0" * 64,
    }
    payload.update(overrides)
    return UploadStart(**payload)


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
    storage.bodies["projects/x/a"] = b"hello"
    assert [chunk async for chunk in storage.stream("projects/x/a")] == [b"hello"]
    assert storage.expiries == [300, 300]


@pytest.mark.asyncio
async def test_download_requires_clean_state() -> None:
    """Changing the state gate would expose quarantined material."""
    from superboss.core.errors import ConflictError
    from superboss.modules.files.service import FileService

    service = FileService(None, None)
    file = File(
        id=uuid4(),
        folder_id=uuid4(),
        filename="report.pdf",
        object_key="x",
        size_bytes=1,
        sha256="0" * 64,
        state=FileState.QUARANTINED,
        uploader_id=uuid4(),
        content_type="application/pdf",
    )
    with pytest.raises(ConflictError) as error:
        await service.ensure_downloadable(file)
    assert error.value.code == "FILE_NOT_READY"


@pytest.mark.asyncio
async def test_same_idempotency_key_reuses_one_active_multipart(db_session, active_owner) -> None:
    """A second identical start must not allocate another external upload."""
    from superboss.modules.files.service import FileService

    storage = InMemoryObjectStorage()
    actor = Actor(active_owner.id, Role.OWNER)
    folder = await add_folder(db_session, active_owner.id)
    command = _start(folder.id)
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
        {"size_bytes": 2},
        {"sha256": "1" * 64},
        {"content_type": "image/png"},
    ],
)
async def test_same_key_with_changed_metadata_conflicts(db_session, active_owner, change) -> None:
    """A fingerprint omission would let one key name two different uploads."""
    from superboss.core.errors import ConflictError
    from superboss.modules.files.service import FileService

    actor = Actor(active_owner.id, Role.OWNER)
    folder = await add_folder(db_session, active_owner.id)
    storage = InMemoryObjectStorage()
    service = FileService(db_session, storage)
    original = _start(folder.id, content_type="application/pdf")
    changed = original.model_copy(update=change)
    await service.start_upload(actor, original, "same")
    with pytest.raises(ConflictError):
        await service.start_upload(actor, changed, "same")
    assert len(storage.active) == 1


@pytest.mark.asyncio
async def test_same_key_is_scoped_to_folder_and_actor(db_session, active_owner) -> None:
    """Global idempotency would wrongly join independent folder/user uploads."""
    from superboss.modules.files.service import FileService

    staff = local_user("file-staff", display_name="Staff")
    db_session.add(staff)
    await db_session.flush()
    first_folder = await add_folder(db_session, active_owner.id, name="one")
    second_folder = await add_folder(db_session, active_owner.id, name="two")
    storage = InMemoryObjectStorage()
    service = FileService(db_session, storage)
    owner = Actor(active_owner.id, Role.OWNER)
    staff_actor = Actor(staff.id, Role.STAFF)
    first = await service.start_upload(owner, _start(first_folder.id), "same")
    second = await service.start_upload(owner, _start(second_folder.id), "same")
    third = await service.start_upload(staff_actor, _start(second_folder.id), "same")
    assert len({first.id, second.id, third.id}) == 3 and len(storage.active) == 3


@pytest.mark.asyncio
async def test_complete_sorts_parts_and_quarantines_with_enqueue(db_session, active_owner) -> None:
    """Completion must persist quarantine, not use multipart ETags as checksums."""
    from superboss.modules.files.service import FileService
    from superboss.modules.files.storage import CompletedPart

    folder = await add_folder(db_session, active_owner.id)
    actor = Actor(active_owner.id, Role.OWNER)
    enqueued: list[UUID] = []
    storage = InMemoryObjectStorage(complete_size=2)
    service = FileService(db_session, storage, lambda file_id, _key: enqueued.append(file_id))
    upload = await service.start_upload(actor, _start(folder.id, size_bytes=2), "complete")
    file = await service.complete_upload(
        actor, upload.id, [CompletedPart(2, "not-a-sha"), CompletedPart(1, "0" * 64)]
    )
    assert file.state.value == "QUARANTINED" and file.sha256 == "0" * 64
    assert storage.completed[upload.multipart_id] == [
        CompletedPart(1, "0" * 64),
        CompletedPart(2, "not-a-sha"),
    ]
    assert enqueued == [file.id]


@pytest.mark.asyncio
async def test_size_mismatch_aborts_and_persists_failed(db_session, active_owner) -> None:
    from superboss.core.errors import ConflictError
    from superboss.modules.files.service import FileService
    from superboss.modules.files.storage import CompletedPart

    folder = await add_folder(db_session, active_owner.id)
    actor = Actor(active_owner.id, Role.OWNER)
    storage = InMemoryObjectStorage(complete_size=2)
    service = FileService(db_session, storage)
    upload = await service.start_upload(actor, _start(folder.id), "mismatch")
    with pytest.raises(ConflictError) as mismatch:
        await service.complete_upload(actor, upload.id, [CompletedPart(1, "e")])
    assert mismatch.value.code == "FILE_UPLOAD_SIZE_MISMATCH"
    file = await db_session.get(File, upload.id)
    assert file is not None
    assert file.state.value == "FAILED" and file.scan_result == "SIZE_MISMATCH"
    assert file.object_key in storage.deleted
    assert upload.multipart_id not in storage.active


@pytest.mark.asyncio
async def test_part_presign_accepts_s3_boundaries(db_session, active_owner) -> None:
    """Changing the S3 boundary would reject valid first or last parts."""
    from superboss.modules.files.service import FileService

    folder = await add_folder(db_session, active_owner.id)
    storage = InMemoryObjectStorage()
    actor = Actor(active_owner.id, Role.OWNER)
    service = FileService(db_session, storage)
    upload = await service.start_upload(actor, _start(folder.id), "parts")
    assert (await service.presign_part(actor, upload.id, 1)).endswith("/1")
    assert (await service.presign_part(actor, upload.id, 10_000)).endswith("/10000")
    assert storage.expiries == [900, 900]


@pytest.mark.asyncio
async def test_part_missing_upload_fails_closed(db_session, active_owner) -> None:
    from superboss.core.errors import NotFoundError
    from superboss.modules.files.service import FileService

    actor = Actor(active_owner.id, Role.OWNER)
    with pytest.raises(NotFoundError):
        await FileService(db_session, InMemoryObjectStorage()).presign_part(actor, uuid4(), 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["QUARANTINED", "SCANNING", "CLEAN", "INFECTED", "FAILED"])
async def test_part_rejects_every_non_uploading_state(db_session, active_owner, state) -> None:
    from superboss.core.errors import ConflictError
    from superboss.modules.files.service import FileService

    folder = await add_folder(db_session, active_owner.id)
    actor = Actor(active_owner.id, Role.OWNER)
    service = FileService(db_session, InMemoryObjectStorage())
    upload = await service.start_upload(actor, _start(folder.id), f"{state}-key")
    file = await db_session.get(File, upload.id)
    assert file is not None
    file.state = FileState(state)
    await db_session.flush()
    with pytest.raises(ConflictError):
        await service.presign_part(actor, upload.id, 1)


@pytest.mark.asyncio
async def test_staff_cannot_presign_part_in_owner_only_folder(db_session, active_owner) -> None:
    from superboss.core.errors import ForbiddenError
    from superboss.modules.files.service import FileService

    staff = local_user("part-staff", display_name="Staff")
    db_session.add(staff)
    await db_session.flush()
    private = await add_folder(
        db_session, active_owner.id, name="老板私有", visibility=FolderVisibility.OWNER_ONLY
    )
    storage = InMemoryObjectStorage()
    service = FileService(db_session, storage)
    owner = Actor(active_owner.id, Role.OWNER)
    upload = await service.start_upload(owner, _start(private.id), "foreign")
    staff_actor = Actor(staff.id, Role.STAFF)
    with pytest.raises(ForbiddenError) as error:
        await service.presign_part(staff_actor, upload.id, 1)
    assert error.value.code == "FOLDER_FORBIDDEN"
    assert storage.expiries == []


@pytest.mark.parametrize("key", ["x", "!" * 255, "", "x" * 256, " x", "x ", "x\r\ny", "中文"])
def test_idempotency_key_grammar(key: str) -> None:
    """Header keys are printable ASCII tokens, never whitespace or controls."""
    import re

    assert bool(re.fullmatch(r"[!-~]{1,255}", key)) == (key in {"x", "!" * 255})


@pytest.mark.asyncio
async def test_storage_error_is_safe_and_leaves_uploading_file(db_session, active_owner) -> None:
    from superboss.core.errors import FileCompletionPendingError
    from superboss.modules.files.service import FileService
    from superboss.modules.files.storage import CompletedPart

    folder = await add_folder(db_session, active_owner.id)
    storage = InMemoryObjectStorage(complete_error=RuntimeError("S3 secret"))
    actor = Actor(active_owner.id, Role.OWNER)
    service = FileService(db_session, storage)
    upload = await service.start_upload(actor, _start(folder.id), "storage-error")
    upload_id = upload.id
    multipart_id = upload.multipart_id
    with pytest.raises(FileCompletionPendingError) as error:
        await service.complete_upload(actor, upload_id, [CompletedPart(1, "e")])
    assert "secret" not in str(error.value).lower()
    file = await db_session.get(File, upload_id)
    assert file is not None and file.state == FileState.UPLOADING
    assert multipart_id in storage.active and multipart_id not in storage.aborted


@pytest.mark.asyncio
async def test_deleted_file_cascades_upload_and_operations_fail_closed(
    db_session, active_owner
) -> None:
    from superboss.core.errors import NotFoundError
    from superboss.modules.files.service import FileService
    from superboss.modules.files.storage import CompletedPart

    folder = await add_folder(db_session, active_owner.id)
    storage = InMemoryObjectStorage()
    actor = Actor(active_owner.id, Role.OWNER)
    service = FileService(db_session, storage)
    upload = await service.start_upload(actor, _start(folder.id), "cascade")
    upload_id = upload.id
    file = await db_session.get(File, upload.id)
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
@pytest.mark.parametrize(
    ("state", "expected_code"),
    [
        ("UPLOADING", "FILE_NOT_READY"),
        ("QUARANTINED", "FILE_NOT_READY"),
        ("SCANNING", "FILE_NOT_READY"),
        ("INFECTED", "FILE_INFECTED"),
        ("FAILED", "FILE_SCAN_FAILED"),
    ],
)
async def test_download_rejects_non_clean_state(
    db_session, active_owner, state, expected_code
) -> None:
    from superboss.core.errors import ConflictError
    from superboss.modules.files.service import FileService

    folder = await add_folder(db_session, active_owner.id)
    file = make_file(
        folder_id=folder.id,
        uploader_id=active_owner.id,
        filename="secret.pdf",
        object_key="projects/x/secret",
        state=FileState(state),
    )
    db_session.add(file)
    await db_session.flush()
    storage = InMemoryObjectStorage()
    actor = Actor(active_owner.id, Role.OWNER)
    with pytest.raises(ConflictError) as error:
        await FileService(db_session, storage).presign_download(actor, file.id)
    assert error.value.code == expected_code
    assert storage.expiries == []


@pytest.mark.asyncio
async def test_clean_download_owner_returns_key_url_with_short_expiry(
    db_session, active_owner
) -> None:
    from superboss.modules.files.service import FileService

    folder = await add_folder(db_session, active_owner.id)
    file = make_file(
        folder_id=folder.id,
        uploader_id=active_owner.id,
        object_key="projects/clean/key",
    )
    db_session.add(file)
    await db_session.flush()
    storage = InMemoryObjectStorage()
    actor = Actor(active_owner.id, Role.OWNER)
    assert (
        await FileService(db_session, storage).presign_download(actor, file.id)
        == "memory://get/projects/clean/key"
    )
    assert storage.expiries == [60]


@pytest.mark.asyncio
async def test_clean_download_staff_all_folder_and_owner_only_denial(
    db_session, active_owner
) -> None:
    from superboss.core.errors import ForbiddenError
    from superboss.modules.files.service import FileService

    staff = local_user("download-staff", display_name="Staff")
    db_session.add(staff)
    await db_session.flush()
    shared = await add_folder(db_session, active_owner.id, name="项目")
    private = await add_folder(
        db_session, active_owner.id, name="老板私有", visibility=FolderVisibility.OWNER_ONLY
    )
    shared_file = make_file(
        folder_id=shared.id,
        uploader_id=active_owner.id,
        object_key="folders/staff/key",
    )
    private_file = make_file(
        folder_id=private.id,
        uploader_id=active_owner.id,
        object_key="folders/private/key",
    )
    db_session.add_all([shared_file, private_file])
    await db_session.flush()
    storage = InMemoryObjectStorage()
    service = FileService(db_session, storage)
    staff_actor = Actor(staff.id, Role.STAFF)
    assert (
        await service.presign_download(staff_actor, shared_file.id)
        == "memory://get/folders/staff/key"
    )
    with pytest.raises(ForbiddenError) as error:
        await service.presign_download(staff_actor, private_file.id)
    assert error.value.code == "FOLDER_FORBIDDEN"
    assert storage.expiries == [60]


@pytest.mark.asyncio
async def test_missing_download_file_fails_closed(db_session, active_owner) -> None:
    from superboss.core.errors import NotFoundError
    from superboss.modules.files.service import FileService

    storage = InMemoryObjectStorage()
    actor = Actor(active_owner.id, Role.OWNER)
    with pytest.raises(NotFoundError):
        await FileService(db_session, storage).presign_download(actor, uuid4())
    assert storage.expiries == []


@pytest.mark.asyncio
async def test_list_folders_seeds_roots_and_filters_by_role(db_session, active_owner) -> None:
    from superboss.modules.files.service import FileService

    staff = local_user("folder-staff", display_name="Staff")
    manager = local_user("folder-manager", display_name="Manager", role=Role.MANAGER)
    db_session.add_all([staff, manager])
    await db_session.flush()
    service = FileService(db_session, InMemoryObjectStorage())
    owner_names = {
        folder.name for folder in await service.list_folders(Actor(active_owner.id, Role.OWNER))
    }
    manager_names = {
        folder.name for folder in await service.list_folders(Actor(manager.id, Role.MANAGER))
    }
    staff_names = {
        folder.name for folder in await service.list_folders(Actor(staff.id, Role.STAFF))
    }
    assert owner_names == {"公司", "项目", "老板私有"}
    assert manager_names == {"公司", "项目"}
    assert staff_names == {"项目"}


@pytest.mark.asyncio
async def test_create_folder_inherits_parent_visibility_and_staff_is_denied(
    db_session, active_owner
) -> None:
    from superboss.core.errors import ForbiddenError
    from superboss.modules.files.schemas import FolderCreate
    from superboss.modules.files.service import FileService

    staff = local_user("mkdir-staff", display_name="Staff")
    db_session.add(staff)
    await db_session.flush()
    service = FileService(db_session, InMemoryObjectStorage())
    owner = Actor(active_owner.id, Role.OWNER)
    roots = {folder.name: folder for folder in await service.list_folders(owner)}
    created = await service.create_folder(
        owner, FolderCreate(parent_id=roots["公司"].id, name="制度")
    )
    assert created.visibility is FolderVisibility.MANAGEMENT
    assert created.parent_id == roots["公司"].id
    with pytest.raises(ForbiddenError) as error:
        await service.create_folder(
            Actor(staff.id, Role.STAFF),
            FolderCreate(parent_id=roots["项目"].id, name="nope"),
        )
    assert error.value.code == "FORBIDDEN"


@pytest.mark.asyncio
async def test_staff_cannot_list_owner_only_files(db_session, active_owner) -> None:
    from superboss.core.errors import ForbiddenError
    from superboss.modules.files.service import FileService

    staff = local_user("list-staff", display_name="Staff")
    db_session.add(staff)
    await db_session.flush()
    private = await add_folder(
        db_session, active_owner.id, name="老板私有", visibility=FolderVisibility.OWNER_ONLY
    )
    with pytest.raises(ForbiddenError) as error:
        await FileService(db_session, InMemoryObjectStorage()).list_files(
            Actor(staff.id, Role.STAFF), private.id
        )
    assert error.value.code == "FOLDER_FORBIDDEN"


@pytest.mark.asyncio
async def test_owner_renames_moves_and_deletes_file(db_session, active_owner) -> None:
    from superboss.modules.files.schemas import FilePatch
    from superboss.modules.files.service import FileService

    shared = await add_folder(db_session, active_owner.id, name="项目")
    dest = await add_folder(
        db_session, active_owner.id, name="公司", visibility=FolderVisibility.MANAGEMENT
    )
    file = make_file(
        folder_id=shared.id,
        uploader_id=active_owner.id,
        filename="old.pdf",
    )
    db_session.add(file)
    await db_session.flush()
    storage = InMemoryObjectStorage()
    service = FileService(db_session, storage)
    owner = Actor(active_owner.id, Role.OWNER)
    renamed = await service.patch_file(owner, file.id, FilePatch(filename="new.pdf"))
    assert renamed.filename == "new.pdf"
    moved = await service.patch_file(owner, file.id, FilePatch(folder_id=dest.id))
    assert moved.folder_id == dest.id
    await service.delete_file(owner, file.id)
    assert await db_session.get(File, file.id) is None
    assert file.object_key in storage.deleted


@pytest.mark.asyncio
async def test_staff_cannot_patch_or_delete_file(db_session, active_owner) -> None:
    from superboss.core.errors import ForbiddenError
    from superboss.modules.files.schemas import FilePatch
    from superboss.modules.files.service import FileService

    staff = local_user("mutate-staff", display_name="Staff")
    db_session.add(staff)
    await db_session.flush()
    folder = await add_folder(db_session, active_owner.id)
    file = make_file(folder_id=folder.id, uploader_id=active_owner.id)
    db_session.add(file)
    await db_session.flush()
    service = FileService(db_session, InMemoryObjectStorage())
    staff_actor = Actor(staff.id, Role.STAFF)
    with pytest.raises(ForbiddenError) as error:
        await service.patch_file(staff_actor, file.id, FilePatch(filename="nope.pdf"))
    assert error.value.code == "FORBIDDEN"
    with pytest.raises(ForbiddenError):
        await service.delete_file(staff_actor, file.id)
    assert await db_session.get(File, file.id) is not None
