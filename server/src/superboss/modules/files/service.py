import asyncio
import hashlib
import inspect
import re
from collections.abc import AsyncGenerator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from superboss.core.actors import Actor, require_owner
from superboss.core.errors import (
    ConflictError,
    FileCompletionPendingError,
    FileProvisioningPendingError,
    ForbiddenError,
    NotFoundError,
)
from superboss.infrastructure.clamav import Scanner, ScanStatus
from superboss.modules.audit.models import AuditLog
from superboss.modules.files.models import File, FileState, Folder, FolderVisibility
from superboss.modules.files.schemas import FilePatch, FolderCreate, UploadStart
from superboss.modules.files.storage import CompletedPart, ObjectStorage
from superboss.modules.users.models import Role, User

_ROOT_FOLDERS = (
    ("公司", FolderVisibility.MANAGEMENT),
    ("项目", FolderVisibility.ALL),
    ("老板私有", FolderVisibility.OWNER_ONLY),
)


def folder_is_visible(actor: Actor, visibility: FolderVisibility) -> bool:
    if actor.role == Role.OWNER:
        return True
    if visibility is FolderVisibility.ALL:
        return actor.role in {Role.MANAGER, Role.STAFF}
    if visibility is FolderVisibility.MANAGEMENT:
        return actor.role == Role.MANAGER
    return False


def require_folder_read(actor: Actor, folder: Folder) -> None:
    if not folder_is_visible(actor, folder.visibility):
        raise ForbiddenError("FOLDER_FORBIDDEN", "You cannot access this folder")


class FileScanService:
    """Scan a quarantined object while holding the File row lock."""

    _TERMINAL_STATES = frozenset({FileState.CLEAN, FileState.INFECTED, FileState.FAILED})
    _MAX_SIGNATURE_LENGTH = 128

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        storage: ObjectStorage,
        scanner: Scanner,
    ) -> None:
        self.session_factory = session_factory
        self.storage = storage
        self.scanner = scanner

    @classmethod
    def _safe_signature(cls, signature: str | None) -> str:
        if signature is None:
            return "INFECTED"
        sanitized = re.sub(r"[^A-Za-z0-9._: +()\-]", "_", signature)
        return sanitized[: cls._MAX_SIGNATURE_LENGTH] or "INFECTED"

    async def scan_file(self, file_id: UUID) -> None:
        object_key = ""
        new_state: FileState | None = None
        async with self.session_factory() as session, session.begin():
            file = await session.scalar(select(File).where(File.id == file_id).with_for_update())
            if file is None or file.state in self._TERMINAL_STATES:
                return
            if file.state != FileState.QUARANTINED:
                return
            object_key = file.object_key
            expected_sha256 = file.sha256
            digest = hashlib.sha256()
            scan_result = "SCAN_FAILED"
            new_state = FileState.FAILED

            async def hashing_chunks() -> AsyncGenerator[bytes]:
                async for chunk in self.storage.stream(object_key):
                    digest.update(chunk)
                    yield chunk

            chunks = hashing_chunks()
            try:
                try:
                    verdict = await self.scanner.scan(chunks)
                finally:
                    await chunks.aclose()
                if digest.hexdigest() != expected_sha256:
                    new_state = FileState.FAILED
                    scan_result = "HASH_MISMATCH"
                elif verdict.status == ScanStatus.CLEAN:
                    new_state = FileState.CLEAN
                    scan_result = "CLEAN"
                elif verdict.status == ScanStatus.INFECTED:
                    new_state = FileState.INFECTED
                    scan_result = self._safe_signature(verdict.signature)
                else:
                    new_state = FileState.FAILED
                    scan_result = "SCAN_FAILED"
            except Exception:  # noqa: BLE001 -- persist only the fixed safe result
                new_state = FileState.FAILED
                scan_result = "SCAN_FAILED"
            file.state = new_state
            file.scan_result = scan_result

        if new_state in {FileState.INFECTED, FileState.FAILED} and object_key:
            try:
                await self.storage.delete_object(object_key)
            except Exception:  # noqa: BLE001 -- scan result is already durable
                return


class FileService:
    _STORAGE_TIMEOUT_SECONDS = 20

    def __init__(
        self,
        session: AsyncSession,
        storage: ObjectStorage | None,
        enqueue_scan: Callable[[UUID, UUID], Awaitable[None] | None] | None = None,
    ) -> None:
        self.session = session
        self.storage = storage
        self.enqueue_scan = enqueue_scan or (lambda _file_id, _delivery_key: None)

    def _storage(self) -> ObjectStorage:
        if self.storage is None:
            raise RuntimeError("Object storage is not configured")
        return self.storage

    async def ensure_downloadable(self, file: File) -> None:
        if file.state == FileState.INFECTED:
            raise ConflictError("FILE_INFECTED", "File did not pass security scanning")
        if file.state == FileState.FAILED:
            raise ConflictError("FILE_SCAN_FAILED", "File scanning did not complete")
        if file.state != FileState.CLEAN:
            raise ConflictError("FILE_NOT_READY", "File is not available for download")

    @staticmethod
    def _segment(value: str, fallback: str) -> str:
        value = value.replace("\\", "/").split("/")[-1]
        value = "".join(
            char if (char.isalnum() or char in {".", "_", "-"}) else "_"
            for char in value
            if ord(char) >= 32 and ord(char) != 127
        ).strip("._")
        return value or fallback

    async def _folder(self, folder_id: UUID) -> Folder:
        folder = await self.session.get(Folder, folder_id)
        if folder is None:
            raise NotFoundError("FOLDER_NOT_FOUND", "Folder not found")
        return folder

    async def start_upload(self, actor: Actor, command: UploadStart, idempotency_key: str) -> File:
        folder = await self._folder(command.folder_id)
        require_folder_read(actor, folder)
        if not re.fullmatch(r"[!-~]{1,255}", idempotency_key):
            raise ValueError("invalid Idempotency-Key")
        fingerprint = self._fingerprint(command)
        existing = await self.session.scalar(
            select(File).where(
                File.folder_id == command.folder_id,
                File.uploader_id == actor.subject_id,
                File.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            if existing.metadata_fingerprint != fingerprint:
                raise ConflictError("FILE_UPLOAD_CONFLICT", "Upload metadata conflicts")
            return await self._provision_upload(existing)
        file_id = uuid4()
        name = self._segment(command.filename, "file")
        key = f"folders/{command.folder_id}/{file_id}/{name}"
        file = File(
            id=file_id,
            folder_id=command.folder_id,
            project_id=command.project_id,
            filename=command.filename,
            object_key=key,
            size_bytes=command.size_bytes,
            sha256=command.sha256,
            uploader_id=actor.subject_id,
            content_type=command.content_type,
            metadata_fingerprint=fingerprint,
            idempotency_key=idempotency_key,
            multipart_id=None,
        )
        self.session.add(file)
        try:
            await self.session.flush()
        except IntegrityError as error:
            is_idempotency = self._is_idempotency_conflict(error)
            await self.session.rollback()
            if not is_idempotency:
                raise
            winner = await self.session.scalar(
                select(File).where(
                    File.folder_id == command.folder_id,
                    File.uploader_id == actor.subject_id,
                    File.idempotency_key == idempotency_key,
                )
            )
            if winner is not None and winner.metadata_fingerprint == fingerprint:
                return await self._provision_upload(winner)
            raise ConflictError("FILE_UPLOAD_CONFLICT", "Upload metadata conflicts") from error
        await self.session.commit()
        return await self._provision_upload(file)

    async def _provision_upload(self, file: File) -> File:
        fresh_file = await self.session.scalar(
            select(File)
            .where(File.id == file.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if fresh_file is None or fresh_file.state != FileState.UPLOADING:
            await self.session.rollback()
            raise FileProvisioningPendingError()
        if fresh_file.multipart_id is not None:
            return fresh_file
        try:
            existing = await asyncio.wait_for(
                self._storage().list_multipart_uploads(fresh_file.object_key),
                timeout=self._STORAGE_TIMEOUT_SECONDS,
            )
        except Exception as error:
            await self.session.rollback()
            raise FileProvisioningPendingError() from error
        if len(existing) == 1:
            fresh_file.multipart_id = existing[0]
            await self.session.commit()
            return fresh_file
        for leftover in existing:
            try:
                await self._storage().abort_multipart(fresh_file.object_key, leftover)
            except Exception:  # noqa: BLE001,S110 -- janitor will retry leftovers
                pass
        try:
            multipart_id = await asyncio.wait_for(
                self._storage().create_multipart(fresh_file.object_key, fresh_file.content_type),
                timeout=self._STORAGE_TIMEOUT_SECONDS,
            )
        except Exception as error:
            await self.session.rollback()
            raise FileProvisioningPendingError() from error
        fresh_file.multipart_id = multipart_id
        await self.session.commit()
        return fresh_file

    @staticmethod
    def _is_idempotency_conflict(error: IntegrityError) -> bool:
        cause = getattr(error.orig, "__cause__", None)
        return getattr(cause, "constraint_name", None) == "uq_files_upload_idempotency"

    @staticmethod
    def _fingerprint(command: UploadStart) -> str:
        material = "\x1f".join(
            (
                command.filename,
                str(command.folder_id),
                str(command.size_bytes),
                command.sha256,
                command.content_type,
            )
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    async def presign_download(self, actor: Actor, file_id: UUID) -> str:
        file = await self.session.get(File, file_id)
        if file is None:
            raise NotFoundError("FILE_NOT_FOUND", "File not found")
        require_folder_read(actor, await self._folder(file.folder_id))
        await self.ensure_downloadable(file)
        return await self._storage().presign_get(file.object_key, 60)

    async def presign_part(self, actor: Actor, upload_id: UUID, part_number: int) -> str:
        if not 1 <= part_number <= 10000:
            raise ValueError("invalid part number")
        file = await self.session.scalar(select(File).where(File.id == upload_id).with_for_update())
        if file is None:
            raise NotFoundError("FILE_UPLOAD_NOT_FOUND", "Upload not found")
        if file.state != FileState.UPLOADING:
            raise ConflictError("FILE_UPLOAD_NOT_ACTIVE", "Upload is not active")
        require_folder_read(actor, await self._folder(file.folder_id))
        if file.multipart_id is None:
            raise ConflictError("FILE_UPLOAD_NOT_ACTIVE", "Upload is not active")
        return await self._storage().presign_upload_part(
            file.object_key, file.multipart_id, part_number, 900
        )

    async def complete_upload(
        self,
        actor: Actor,
        upload_id: UUID,
        parts: list[CompletedPart],
        request_id: UUID | None = None,
    ) -> File:
        file = await self.session.scalar(
            select(File)
            .where(File.id == upload_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if file is None:
            raise NotFoundError("FILE_UPLOAD_NOT_FOUND", "Upload not found")
        require_folder_read(actor, await self._folder(file.folder_id))
        return await self._finish_upload(actor, file, parts, request_id)

    async def _finish_upload(
        self,
        actor: Actor,
        file: File,
        parts: list[CompletedPart],
        request_id: UUID | None,
    ) -> File:
        if file.state != FileState.UPLOADING:
            return file
        if file.multipart_id is None:
            raise ConflictError("FILE_UPLOAD_NOT_ACTIVE", "Upload is not active")
        if len({part.part_number for part in parts}) != len(parts):
            raise ValueError("duplicate part number")
        canonical = sorted(parts, key=lambda part: part.part_number)
        try:
            metadata = await asyncio.wait_for(
                self._storage().complete_multipart(file.object_key, file.multipart_id, canonical),
                timeout=self._STORAGE_TIMEOUT_SECONDS,
            )
        except Exception as error:
            await self.session.rollback()
            raise FileCompletionPendingError() from error
        if metadata.size_bytes != file.size_bytes:
            file.state = FileState.FAILED
            file.scan_result = "SIZE_MISMATCH"
            await self.session.commit()
            try:
                await self._storage().delete_object(file.object_key)
            except Exception:  # noqa: BLE001,S110 -- hourly janitor retries leftover objects
                pass
            raise ConflictError(
                "FILE_UPLOAD_SIZE_MISMATCH", "Uploaded object size does not match declared size"
            )
        from superboss.core.config import get_settings

        if get_settings().scan_enabled:
            file.state = FileState.QUARANTINED
        else:
            file.state = FileState.CLEAN
            file.scan_result = "SCAN_SKIPPED"
        if request_id is not None:
            self.session.add(
                AuditLog(
                    actor_kind="user",
                    actor_id=actor.subject_id,
                    action="file.upload.complete",
                    object_type="file",
                    object_id=file.id,
                    project_id=file.project_id,
                    outcome="SUCCESS",
                    metadata_json={
                        "state": file.state.value,
                        "size_bytes": file.size_bytes,
                        "actor_role": actor.role.value if actor.role is not None else None,
                    },
                    request_id=request_id,
                )
            )
        await self.session.commit()
        if file.state == FileState.QUARANTINED:
            await _safe_enqueue(self.enqueue_scan, file.id)
        return file

    async def ensure_roots(self, actor: Actor) -> None:
        owner = await self.session.scalar(select(User).where(User.role == Role.OWNER))
        created_by = owner.id if owner is not None else actor.subject_id
        existing = {
            folder.name: folder
            for folder in (
                await self.session.scalars(select(Folder).where(Folder.parent_id.is_(None)))
            ).all()
        }
        for name, visibility in _ROOT_FOLDERS:
            if name in existing:
                continue
            self.session.add(
                Folder(
                    name=name,
                    visibility=visibility,
                    created_by=created_by,
                    parent_id=None,
                )
            )
        await self.session.flush()

    async def list_folders(self, actor: Actor) -> list[Folder]:
        await self.ensure_roots(actor)
        folders = list((await self.session.scalars(select(Folder).order_by(Folder.name))).all())
        return [folder for folder in folders if folder_is_visible(actor, folder.visibility)]

    async def create_folder(self, actor: Actor, command: FolderCreate) -> Folder:
        require_owner(actor)
        parent = await self._folder(command.parent_id)
        folder = Folder(
            parent_id=parent.id,
            name=command.name,
            visibility=parent.visibility,
            created_by=actor.subject_id,
        )
        self.session.add(folder)
        await self.session.flush()
        return folder

    async def list_files(self, actor: Actor, folder_id: UUID) -> list[File]:
        folder = await self._folder(folder_id)
        require_folder_read(actor, folder)
        statement = (
            select(File)
            .where(File.folder_id == folder_id, File.state != FileState.UPLOADING)
            .order_by(File.filename, File.created_at)
        )
        return list((await self.session.scalars(statement)).all())

    async def patch_file(self, actor: Actor, file_id: UUID, command: FilePatch) -> File:
        require_owner(actor)
        file = await self.session.get(File, file_id)
        if file is None:
            raise NotFoundError("FILE_NOT_FOUND", "File not found")
        if command.filename is not None:
            file.filename = command.filename
        if command.folder_id is not None:
            dest = await self._folder(command.folder_id)
            require_folder_read(actor, dest)
            file.folder_id = dest.id
        await self.session.flush()
        return file

    async def delete_file(self, actor: Actor, file_id: UUID) -> None:
        require_owner(actor)
        file = await self.session.get(File, file_id)
        if file is None:
            raise NotFoundError("FILE_NOT_FOUND", "File not found")
        object_key = file.object_key
        await self.session.delete(file)
        await self.session.flush()
        try:
            await self._storage().delete_object(object_key)
        except Exception:  # noqa: BLE001 -- row removal is the authorization outcome
            return


class StaleUploadService:
    """Expire abandoned uploads and re-dispatch lost scan jobs."""

    _EXPIRY_AGE = timedelta(hours=24)
    _SCAN_RETRY_AGE = timedelta(minutes=15)

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        storage: ObjectStorage | None = None,
        enqueue_scan: Callable[[UUID, UUID], Awaitable[None] | None] | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.storage = storage
        self.enqueue_scan = enqueue_scan or (lambda _file_id, _delivery_key: None)

    async def recover_stale_uploads(
        self,
        *,
        now: datetime | None = None,
        limit: int = 100,
    ) -> int:
        if limit < 1:
            raise ValueError("stale upload recovery limit must be positive")
        reference = now or datetime.now(UTC)
        if reference.tzinfo is None:
            raise ValueError("stale upload recovery time must be timezone-aware")
        recovered = 0
        recovered += await self._expire_uploads(reference, limit)
        recovered += await self._redispatch_scans(reference, limit)
        return recovered

    async def _expire_uploads(self, reference: datetime, limit: int) -> int:
        cutoff = reference - self._EXPIRY_AGE
        async with self.session_factory() as session:
            candidate_ids = list(
                await session.scalars(
                    select(File.id)
                    .where(File.state == FileState.UPLOADING, File.created_at < cutoff)
                    .order_by(File.created_at, File.id)
                    .limit(limit)
                )
            )
        recovered = 0
        for file_id in candidate_ids:
            if await self._expire_one(file_id, cutoff):
                recovered += 1
        return recovered

    async def _expire_one(self, file_id: UUID, cutoff: datetime) -> bool:
        object_key = ""
        multipart_id: str | None = None
        async with self.session_factory() as session, session.begin():
            file = await session.scalar(select(File).where(File.id == file_id).with_for_update())
            if file is None or file.state != FileState.UPLOADING:
                return False
            if file.created_at >= cutoff:
                return False
            file.state = FileState.FAILED
            file.scan_result = "UPLOAD_EXPIRED"
            object_key = file.object_key
            multipart_id = file.multipart_id
        if self.storage is not None:
            if multipart_id is not None:
                try:
                    await self.storage.abort_multipart(object_key, multipart_id)
                except Exception:  # noqa: BLE001,S110 -- expiry is already durable
                    pass
            try:
                await self.storage.delete_object(object_key)
            except Exception:  # noqa: BLE001,S110 -- expiry is already durable
                pass
        return True

    async def _redispatch_scans(self, reference: datetime, limit: int) -> int:
        cutoff = reference - self._SCAN_RETRY_AGE
        async with self.session_factory() as session:
            file_ids = list(
                await session.scalars(
                    select(File.id)
                    .where(File.state == FileState.QUARANTINED, File.updated_at < cutoff)
                    .order_by(File.updated_at, File.id)
                    .limit(limit)
                )
            )
        for file_id in file_ids:
            await _safe_enqueue(self.enqueue_scan, file_id)
        return len(file_ids)


async def _safe_enqueue(
    enqueue_scan: Callable[[UUID, UUID], Awaitable[None] | None],
    file_id: UUID,
) -> None:
    try:
        result = enqueue_scan(file_id, file_id)
        if inspect.isawaitable(result):
            await result
    except Exception:  # noqa: BLE001 -- hourly janitor re-dispatches quarantined files
        return
