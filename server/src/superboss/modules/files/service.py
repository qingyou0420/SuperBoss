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

from superboss.core.actors import Actor, require_project_access
from superboss.core.errors import (
    ConflictError,
    FileCompletionPendingError,
    FileProvisioningPendingError,
    ForbiddenError,
    NotFoundError,
)
from superboss.infrastructure.clamav import Scanner, ScanStatus
from superboss.modules.audit.models import AuditLog
from superboss.modules.files.models import File, FileState
from superboss.modules.files.schemas import UploadStart
from superboss.modules.files.storage import CompletedPart, ObjectStorage


class FileScanService:
    """Scan a quarantined object without holding the File row lock during I/O."""

    _TERMINAL_STATES = frozenset(
        {FileState.CLEAN, FileState.INFECTED, FileState.FAILED}
    )
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
        expected_sha256 = ""
        async with self.session_factory() as session, session.begin():
            file = await session.scalar(
                select(File).where(File.id == file_id).with_for_update()
            )
            if file is None or file.state in self._TERMINAL_STATES:
                return
            if file.state != FileState.QUARANTINED:
                return
            file.state = FileState.SCANNING
            object_key = file.object_key
            expected_sha256 = file.sha256

        digest = hashlib.sha256()
        new_state = FileState.FAILED
        scan_result = "SCAN_FAILED"
        try:

            async def hashing_chunks() -> AsyncGenerator[bytes]:
                async for chunk in self.storage.stream(object_key):
                    digest.update(chunk)
                    yield chunk

            chunks = hashing_chunks()
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

        async with self.session_factory() as session, session.begin():
            file = await session.scalar(
                select(File).where(File.id == file_id).with_for_update()
            )
            if file is None or file.state != FileState.SCANNING:
                return
            file.state = new_state
            file.scan_result = scan_result

        if new_state in {FileState.INFECTED, FileState.FAILED}:
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

    async def start_upload(
        self, actor: Actor, command: UploadStart, idempotency_key: str
    ) -> File:
        require_project_access(actor, command.project_id)
        return await self._start_upload(actor, command, idempotency_key)

    async def start_import_upload(
        self, actor: Actor, command: UploadStart, idempotency_key: str
    ) -> File:
        if (
            actor.kind != "device"
            or actor.role is not None
            or command.project_id not in actor.project_ids
            or "imports:create" not in actor.scopes
        ):
            raise ForbiddenError("IMPORT_CREATE_FORBIDDEN", "Import creation is not permitted")
        return await self._start_upload(actor, command, idempotency_key)

    async def _start_upload(
        self, actor: Actor, command: UploadStart, idempotency_key: str
    ) -> File:
        if not re.fullmatch(r"[!-~]{1,255}", idempotency_key):
            raise ValueError("invalid Idempotency-Key")
        fingerprint = self._fingerprint(command)
        existing = await self.session.scalar(
            select(File).where(
                File.project_id == command.project_id,
                File.uploader_kind == actor.kind,
                File.uploader_id == actor.subject_id,
                File.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            if existing.metadata_fingerprint != fingerprint:
                raise ConflictError("FILE_UPLOAD_CONFLICT", "Upload metadata conflicts")
            return await self._provision_upload(existing)
        file_id = uuid4()
        category = self._segment(command.category, "uncategorized")
        name = self._segment(command.filename, "file")
        key = (
            f"projects/{command.project_id}/{category}/"
            f"{command.file_date.isoformat()}/{file_id}/{name}"
        )
        file = File(
            id=file_id,
            project_id=command.project_id,
            filename=command.filename,
            category=command.category,
            file_date=command.file_date,
            object_key=key,
            size_bytes=command.size_bytes,
            sha256=command.sha256,
            uploader_id=actor.subject_id,
            uploader_kind=actor.kind,
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
                    File.project_id == command.project_id,
                    File.uploader_kind == actor.kind,
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
                self._storage().create_multipart(
                    fresh_file.object_key, fresh_file.content_type
                ),
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
                command.category,
                command.file_date.isoformat(),
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
        require_project_access(actor, file.project_id)
        await self.ensure_downloadable(file)
        return await self._storage().presign_get(file.object_key, 60)

    async def presign_part(self, actor: Actor, upload_id: UUID, part_number: int) -> str:
        return await self._presign_part(actor, upload_id, part_number, import_device=False)

    async def presign_import_part(
        self, actor: Actor, upload_id: UUID, part_number: int
    ) -> str:
        return await self._presign_part(actor, upload_id, part_number, import_device=True)

    async def _presign_part(
        self,
        actor: Actor,
        upload_id: UUID,
        part_number: int,
        *,
        import_device: bool,
    ) -> str:
        if not 1 <= part_number <= 10000:
            raise ValueError("invalid part number")
        file = await self.session.scalar(
            select(File).where(File.id == upload_id).with_for_update()
        )
        if file is None:
            raise NotFoundError("FILE_UPLOAD_NOT_FOUND", "Upload not found")
        if file.state != FileState.UPLOADING:
            raise ConflictError("FILE_UPLOAD_NOT_ACTIVE", "Upload is not active")
        self._authorize_upload_operation(actor, file.project_id, import_device=import_device)
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
        return await self._complete_upload(
            actor, upload_id, parts, request_id, import_device=False
        )

    async def complete_import_upload(
        self,
        actor: Actor,
        upload_id: UUID,
        parts: list[CompletedPart],
        request_id: UUID | None = None,
    ) -> File:
        return await self._complete_upload(
            actor, upload_id, parts, request_id, import_device=True
        )

    async def _complete_upload(
        self,
        actor: Actor,
        upload_id: UUID,
        parts: list[CompletedPart],
        request_id: UUID | None,
        *,
        import_device: bool,
    ) -> File:
        file = await self.session.scalar(
            select(File)
            .where(File.id == upload_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if file is None:
            raise NotFoundError("FILE_UPLOAD_NOT_FOUND", "Upload not found")
        self._authorize_upload_operation(actor, file.project_id, import_device=import_device)
        if file.state != FileState.UPLOADING:
            return file
        if file.multipart_id is None:
            raise ConflictError("FILE_UPLOAD_NOT_ACTIVE", "Upload is not active")
        if len({part.part_number for part in parts}) != len(parts):
            raise ValueError("duplicate part number")
        canonical = sorted(parts, key=lambda part: part.part_number)
        try:
            metadata = await asyncio.wait_for(
                self._storage().complete_multipart(
                    file.object_key, file.multipart_id, canonical
                ),
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
        file.state = FileState.QUARANTINED
        if request_id is not None:
            self.session.add(
                AuditLog(
                    actor_kind=actor.kind,
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
        await _safe_enqueue(self.enqueue_scan, file.id)
        return file

    @staticmethod
    def _authorize_upload_operation(
        actor: Actor,
        project_id: UUID,
        *,
        import_device: bool,
    ) -> None:
        if not import_device:
            require_project_access(actor, project_id)
            return
        if (
            actor.kind != "device"
            or actor.role is not None
            or project_id not in actor.project_ids
            or "imports:upload" not in actor.scopes
        ):
            raise ForbiddenError("IMPORT_UPLOAD_FORBIDDEN", "Import upload is not permitted")


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
            file = await session.scalar(
                select(File).where(File.id == file_id).with_for_update()
            )
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

