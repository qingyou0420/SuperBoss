import hashlib
import re
from collections.abc import Awaitable, Callable
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from superboss.core.actors import Actor, require_project_access
from superboss.core.errors import (
    ConflictError,
    DomainError,
    FileNotFoundError,
    FileProvisioningPendingError,
    FileStorageFailureError,
    FileUploadSizeMismatchError,
    NotFoundError,
)
from superboss.modules.files.models import (
    File,
    FileState,
    FileStorageCleanup,
    FileUploadLifecycle,
    Upload,
)
from superboss.modules.files.schemas import UploadStart
from superboss.modules.files.storage import CompletedPart, ObjectStorage


class FileNotReadyError(ConflictError):
    def __init__(self) -> None:
        super().__init__()
        self.code = "FILE_NOT_READY"
        self.message = "File is not available for download"


class FileUploadConflictError(ConflictError):
    def __init__(self) -> None:
        DomainError.__init__(self, "FILE_UPLOAD_CONFLICT", "Upload metadata conflicts", 409)


class FileUploadNotFoundError(NotFoundError):
    def __init__(self) -> None:
        DomainError.__init__(self, "FILE_UPLOAD_NOT_FOUND", "Upload not found", 404)


class FileUploadNotActiveError(ConflictError):
    def __init__(self) -> None:
        DomainError.__init__(self, "FILE_UPLOAD_NOT_ACTIVE", "Upload is not active", 409)


class FileUploadProjectMismatchError(FileUploadConflictError):
    def __init__(self) -> None:
        DomainError.__init__(
            self, "FILE_UPLOAD_PROJECT_MISMATCH", "Upload project does not match file", 409
        )


class FileService:
    def __init__(
        self,
        session: AsyncSession,
        storage: ObjectStorage | None,
        enqueue_scan: Callable[[UUID], Awaitable[None] | None] | None = None,
    ) -> None:
        self.session = session
        self.storage = storage
        self.enqueue_scan = enqueue_scan or (lambda _file_id: None)

    def _storage(self) -> ObjectStorage:
        if self.storage is None:
            raise RuntimeError("Object storage is not configured")
        return self.storage

    async def ensure_downloadable(self, file: File) -> None:
        if file.state != FileState.CLEAN:
            raise FileNotReadyError()

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
    ) -> Upload:
        require_project_access(actor, command.project_id)
        if not re.fullmatch(r"[!-~]{1,255}", idempotency_key):
            raise ValueError("invalid Idempotency-Key")
        existing = await self.session.scalar(
            select(Upload).where(
                Upload.project_id == command.project_id,
                Upload.uploader_kind == actor.kind,
                Upload.uploader_id == actor.subject_id,
                Upload.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            file = await self.session.get(File, existing.file_id)
            if file is None or existing.metadata_fingerprint != self._fingerprint(command):
                raise FileUploadConflictError()
            return await self._provision_upload(existing, file)
        file_id = uuid4()
        upload_id = uuid4()
        category = self._segment(command.category, "uncategorized")
        name = self._segment(command.filename, "file")
        key = f"projects/{command.project_id}/{category}/{command.file_date.isoformat()}/{file_id}/{name}"
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
        )
        upload = Upload(
            id=upload_id,
            file_id=file_id,
            project_id=command.project_id,
            uploader_id=actor.subject_id,
            uploader_kind=actor.kind,
            metadata_fingerprint=self._fingerprint(command),
            idempotency_key=idempotency_key,
            multipart_id=None,
        )
        lifecycle = FileUploadLifecycle(
            upload_id=upload_id,
            file_id=file_id,
            project_id=command.project_id,
            object_key=key,
            multipart_id=None,
            content_type=command.content_type,
            declared_size_bytes=command.size_bytes,
            provision_state="PROVISIONING",
            completion_state="NONE",
        )
        self.session.add_all([file, upload, lifecycle])
        try:
            await self.session.flush()
        except IntegrityError as error:
            is_idempotency = self._is_idempotency_conflict(error)
            await self.session.rollback()
            if not is_idempotency:
                raise
            winner = await self.session.scalar(
                select(Upload).where(
                    Upload.project_id == command.project_id,
                    Upload.uploader_kind == actor.kind,
                    Upload.uploader_id == actor.subject_id,
                    Upload.idempotency_key == idempotency_key,
                )
            )
            if winner is not None and winner.metadata_fingerprint == self._fingerprint(command):
                winner_file = await self.session.get(File, winner.file_id)
                if winner_file is not None:
                    return await self._provision_upload(winner, winner_file)
            raise FileUploadConflictError() from error
        await self.session.commit()
        return await self._provision_upload(upload, file)

    async def _provision_upload(self, upload: Upload, file: File) -> Upload:
        lifecycle = await self.session.scalar(
            select(FileUploadLifecycle)
            .where(FileUploadLifecycle.upload_id == upload.id)
            .with_for_update()
        )
        if lifecycle is None:
            raise FileProvisioningPendingError()
        if lifecycle.provision_state == "READY":
            if upload.multipart_id is not None and lifecycle.multipart_id == upload.multipart_id:
                return upload
            raise FileProvisioningPendingError()
        if lifecycle.provision_state != "PROVISIONING":
            raise FileProvisioningPendingError()
        try:
            multipart_ids = await self._storage().list_multipart_uploads(lifecycle.object_key)
        except Exception as error:
            await self.session.rollback()
            raise FileProvisioningPendingError() from error
        if len(multipart_ids) > 1:
            for multipart_id in multipart_ids:
                await self._record_multipart_cleanup(lifecycle, multipart_id)
            await self.session.commit()
            raise FileProvisioningPendingError()
        if len(multipart_ids) == 1:
            return await self._bind_multipart(upload, lifecycle, multipart_ids[0])
        try:
            multipart_id = await self._storage().create_multipart(
                lifecycle.object_key, lifecycle.content_type
            )
        except Exception as error:
            await self.session.rollback()
            raise FileProvisioningPendingError() from error
        return await self._bind_multipart(upload, lifecycle, multipart_id)

    async def _bind_multipart(
        self, upload: Upload, lifecycle: FileUploadLifecycle, multipart_id: str
    ) -> Upload:
        if not multipart_id:
            await self.session.rollback()
            raise FileProvisioningPendingError()
        upload.multipart_id = multipart_id
        lifecycle.multipart_id = multipart_id
        lifecycle.provision_state = "READY"
        try:
            await self.session.commit()
        except Exception as error:
            await self.session.rollback()
            raise FileProvisioningPendingError() from error
        return upload

    async def _record_multipart_cleanup(
        self, lifecycle: FileUploadLifecycle, multipart_id: str
    ) -> None:
        dedupe_key = hashlib.sha256(
            f"ABORT_MULTIPART\x1f{lifecycle.object_key}\x1f{multipart_id}".encode()
        ).hexdigest()
        existing = await self.session.scalar(
            select(FileStorageCleanup).where(
                FileStorageCleanup.operation == "ABORT_MULTIPART",
                FileStorageCleanup.dedupe_key == dedupe_key,
            )
        )
        if existing is None:
            self.session.add(
                FileStorageCleanup(
                    operation="ABORT_MULTIPART",
                    dedupe_key=dedupe_key,
                    object_key=lifecycle.object_key,
                    multipart_id=multipart_id,
                    lifecycle_id=lifecycle.upload_id,
                )
            )

    @staticmethod
    def _is_idempotency_conflict(error: IntegrityError) -> bool:
        cause = getattr(error.orig, "__cause__", None)
        return getattr(cause, "constraint_name", None) == "uq_upload_idempotency"

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
            raise FileNotFoundError()
        require_project_access(actor, file.project_id)
        await self.ensure_downloadable(file)
        return await self._storage().presign_get(file.object_key, 60)

    async def presign_part(self, actor: Actor, upload_id: UUID, part_number: int) -> str:
        if not 1 <= part_number <= 10000:
            raise ValueError("invalid part number")
        upload = await self.session.get(Upload, upload_id)
        if upload is None:
            raise FileUploadNotFoundError()
        file = await self.session.get(File, upload.file_id)
        if file is not None and file.project_id != upload.project_id:
            raise FileUploadProjectMismatchError()
        if file is None or file.state != FileState.UPLOADING:
            raise FileUploadNotActiveError()
        require_project_access(actor, file.project_id)
        if upload.multipart_id is None:
            raise FileUploadNotActiveError()
        return await self._storage().presign_upload_part(
            file.object_key, upload.multipart_id, part_number, 900
        )

    async def complete_upload(
        self, actor: Actor, upload_id: UUID, parts: list[CompletedPart]
    ) -> File:
        upload = await self.session.scalar(
            select(Upload).where(Upload.id == upload_id).with_for_update()
        )
        if upload is None:
            raise FileUploadNotFoundError()
        file = await self.session.get(File, upload.file_id)
        if file is not None and file.project_id != upload.project_id:
            raise FileUploadProjectMismatchError()
        if file is None or file.state != FileState.UPLOADING:
            raise FileUploadNotActiveError()
        require_project_access(actor, file.project_id)
        if upload.multipart_id is None:
            raise FileUploadNotActiveError()
        if len({p.part_number for p in parts}) != len(parts):
            raise ValueError("duplicate part number")
        try:
            metadata = await self._storage().complete_multipart(file.object_key, upload.multipart_id, sorted(parts, key=lambda p: p.part_number))
        except Exception as error:
            await self._fail_upload(file, upload.multipart_id)
            raise FileStorageFailureError() from error
        if metadata.size_bytes != file.size_bytes:
            await self._fail_upload(file, upload.multipart_id)
            raise FileUploadSizeMismatchError()
        file.state = FileState.QUARANTINED
        await self.session.flush()
        return file

    async def _fail_upload(self, file: File, multipart_id: str) -> None:
        try:
            await self._storage().abort_multipart(file.object_key, multipart_id)
        except Exception:  # noqa: BLE001 -- abort failures must not prevent durable FAILED state
            file.scan_result = "abort_failed"
        file.state = FileState.FAILED
        await self.session.commit()
