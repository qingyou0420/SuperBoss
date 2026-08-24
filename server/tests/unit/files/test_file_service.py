"""File upload state-machine behavior."""

from datetime import date
from uuid import UUID, uuid4

import pytest

from superboss.core.actors import Actor
from superboss.modules.projects.models import Project, ProjectMember
from superboss.modules.users.models import Role
from tests.files.storage import InMemoryObjectStorage
from tests.identity import local_user


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
async def test_generic_start_upload_rejects_device_with_project_and_import_scope(
    db_session, active_owner
) -> None:
    """The browser File API boundary must stay closed even to a fully granted import device."""
    from superboss.core.errors import ForbiddenError
    from superboss.modules.files.schemas import UploadStart
    from superboss.modules.files.service import FileService

    project = Project(name="Generic upload remains browser-only")
    db_session.add(project)
    await db_session.flush()
    actor = Actor(
        "device",
        uuid4(),
        None,
        frozenset({project.id}),
        frozenset({"imports:create", "imports:upload"}),
    )
    storage = InMemoryObjectStorage()

    with pytest.raises(ForbiddenError):
        await FileService(db_session, storage).start_upload(
            actor,
            UploadStart(
                project_id=project.id,
                filename="k3.json",
                size_bytes=1,
                sha256="0" * 64,
                category="kimi-imports",
                file_date="2026-08-09",
                content_type="application/json",
            ),
            "generic-device-denied",
        )

    assert storage.create_calls == 0 and storage.active == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("actor_case", ["valid", "kind", "role", "project", "scope"])
async def test_import_start_upload_requires_exact_import_create_actor(
    db_session, active_owner, actor_case: str
) -> None:
    """Only a roleless device with this project and imports:create may use the narrow entry."""
    from superboss.core.errors import ForbiddenError
    from superboss.modules.files.schemas import UploadStart
    from superboss.modules.files.service import FileService

    project = Project(name=f"Import upload actor {actor_case}")
    db_session.add(project)
    await db_session.flush()
    subject_id = uuid4()
    actor = Actor(
        "device",
        subject_id,
        None,
        frozenset({project.id}),
        frozenset({"imports:create", "imports:upload"}),
    )
    if actor_case == "kind":
        actor = Actor("user", subject_id, None, actor.project_ids, actor.scopes)
    elif actor_case == "role":
        actor = Actor("device", subject_id, Role.OWNER, actor.project_ids, actor.scopes)
    elif actor_case == "project":
        actor = Actor("device", subject_id, None, frozenset(), actor.scopes)
    elif actor_case == "scope":
        actor = Actor(
            "device",
            subject_id,
            None,
            actor.project_ids,
            frozenset({"imports:upload"}),
        )
    command = UploadStart(
        project_id=project.id,
        filename="k3.json",
        size_bytes=1,
        sha256="0" * 64,
        category="kimi-imports",
        file_date="2026-08-09",
        content_type="application/json",
    )
    storage = InMemoryObjectStorage()
    service = FileService(db_session, storage)

    if actor_case == "valid":
        upload = await service.start_import_upload(actor, command, "import-device-allowed")
        assert upload.uploader_kind == "device" and upload.uploader_id == subject_id
        assert storage.create_calls == 1 and len(storage.active) == 1
    else:
        with pytest.raises(ForbiddenError):
            await service.start_import_upload(actor, command, f"import-device-{actor_case}")
        assert storage.create_calls == 0 and storage.active == {}


@pytest.mark.asyncio
async def test_generic_part_and_completion_remain_closed_to_import_device(
    db_session, active_owner
) -> None:
    """A fully scoped import device still cannot call either generic FileService operation."""
    from superboss.core.errors import ForbiddenError
    from superboss.modules.files.schemas import UploadStart
    from superboss.modules.files.service import FileService
    from superboss.modules.files.storage import CompletedPart

    project = Project(name="Generic attachment operations remain browser-only")
    db_session.add(project)
    await db_session.flush()
    actor = Actor(
        "device",
        uuid4(),
        None,
        frozenset({project.id}),
        frozenset({"imports:create", "imports:upload"}),
    )
    storage = InMemoryObjectStorage(complete_size=1)
    service = FileService(db_session, storage)
    upload = await service.start_import_upload(
        actor,
        UploadStart(
            project_id=project.id,
            filename="k3.json",
            size_bytes=1,
            sha256="0" * 64,
            category="kimi-imports",
            file_date="2026-08-09",
            content_type="application/json",
        ),
        "generic-attachment-device-denied",
    )

    with pytest.raises(ForbiddenError):
        await service.presign_part(actor, upload.id, 1)
    with pytest.raises(ForbiddenError):
        await service.complete_upload(actor, upload.id, [CompletedPart(1, "private-etag")])

    assert storage.expiries == [] and storage.complete_calls == 0 and storage.completed == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["part", "complete"])
@pytest.mark.parametrize("actor_case", ["valid", "kind", "role", "project", "scope"])
async def test_import_attachment_file_entries_require_exact_upload_actor(
    db_session, active_owner, operation: str, actor_case: str
) -> None:
    """Narrow FileService entries authorize imports:upload before sharing Task 7 internals."""
    from superboss.core.errors import ForbiddenError
    from superboss.modules.files.schemas import UploadStart
    from superboss.modules.files.service import FileService
    from superboss.modules.files.storage import CompletedPart

    project = Project(name=f"Import attachment actor {operation} {actor_case}")
    db_session.add(project)
    await db_session.flush()
    subject_id = uuid4()
    creator = Actor(
        "device",
        subject_id,
        None,
        frozenset({project.id}),
        frozenset({"imports:create", "imports:upload"}),
    )
    storage = InMemoryObjectStorage(complete_size=1)
    service = FileService(db_session, storage)
    upload = await service.start_import_upload(
        creator,
        UploadStart(
            project_id=project.id,
            filename="k3.json",
            size_bytes=1,
            sha256="0" * 64,
            category="kimi-imports",
            file_date="2026-08-09",
            content_type="application/json",
        ),
        f"import-attachment-{operation}-{actor_case}",
    )
    actor = Actor(
        "device",
        subject_id,
        None,
        frozenset({project.id}),
        frozenset({"imports:upload"}),
    )
    if actor_case == "kind":
        actor = Actor("user", subject_id, None, actor.project_ids, actor.scopes)
    elif actor_case == "role":
        actor = Actor("device", subject_id, Role.OWNER, actor.project_ids, actor.scopes)
    elif actor_case == "project":
        actor = Actor("device", subject_id, None, frozenset(), actor.scopes)
    elif actor_case == "scope":
        actor = Actor(
            "device", subject_id, None, actor.project_ids, frozenset({"imports:create"})
        )

    async def invoke() -> object:
        if operation == "part":
            return await service.presign_import_part(actor, upload.id, 1)
        return await service.complete_import_upload(
            actor,
            upload.id,
            [CompletedPart(1, "private-etag")],
            uuid4(),
        )

    if actor_case == "valid":
        result = await invoke()
        if operation == "part":
            assert isinstance(result, str) and result.endswith("/1")
            assert storage.expiries == [900]
        else:
            assert result.state.value == "QUARANTINED"
            assert storage.complete_calls == 1
    else:
        with pytest.raises(ForbiddenError):
            await invoke()
        assert storage.expiries == [] and storage.complete_calls == 0


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
    staff = local_user("file-staff", display_name="Staff")
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
async def test_complete_sorts_parts_and_quarantines_with_enqueue(
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
    service = FileService(
        db_session, storage, lambda file_id, _key: enqueued.append(file_id)
    )
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
    assert enqueued == [file.id]


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
    from superboss.modules.files.models import File

    file = await db_session.get(File, upload.file_id)
    assert file is not None
    assert file.state.value == "FAILED" and file.scan_result == "SIZE_MISMATCH"
    assert file.object_key in storage.deleted
    assert upload.multipart_id not in storage.active


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
    staff = local_user("part-staff", display_name="Staff")
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
async def test_storage_error_is_safe_and_leaves_uploading_file(
    db_session, active_owner
) -> None:
    from superboss.core.errors import FileCompletionPendingError
    from superboss.modules.files.models import File, FileState
    from superboss.modules.files.schemas import UploadStart
    from superboss.modules.files.service import FileService
    from superboss.modules.files.storage import CompletedPart

    project = Project(name="Storage error")
    db_session.add(project)
    await db_session.flush()
    storage = InMemoryObjectStorage(complete_error=RuntimeError("S3 secret"))
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
    assert file is not None and file.state == FileState.UPLOADING
    assert upload.multipart_id in storage.active and upload.multipart_id not in storage.aborted


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
@pytest.mark.parametrize(
    ("state", "expected_error"),
    [
        ("UPLOADING", "FileNotReadyError"),
        ("QUARANTINED", "FileNotReadyError"),
        ("SCANNING", "FileNotReadyError"),
        ("INFECTED", "FileInfectedError"),
        ("FAILED", "FileScanFailedError"),
    ],
)
async def test_download_rejects_non_clean_state(
    db_session, active_owner, state, expected_error
) -> None:
    from superboss.modules.files.models import File, FileState
    from superboss.modules.files.service import (
        FileInfectedError,
        FileNotReadyError,
        FileScanFailedError,
        FileService,
    )

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
    error_types = {
        "FileInfectedError": FileInfectedError,
        "FileNotReadyError": FileNotReadyError,
        "FileScanFailedError": FileScanFailedError,
    }
    with pytest.raises(error_types[expected_error]):
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
    staff = local_user("download-staff", display_name="Staff")
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
