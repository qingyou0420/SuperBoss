import asyncio
import hashlib
import json
import re
from collections.abc import AsyncGenerator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import and_, case, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from superboss.core.actors import Actor, require_project_access
from superboss.core.errors import (
    ConflictError,
    DomainError,
    FileCompletionPendingError,
    FileNotFoundError,
    FileProvisioningPendingError,
    FileUploadSizeMismatchError,
    NotFoundError,
)
from superboss.infrastructure.clamav import Scanner, ScanStatus
from superboss.modules.audit.schemas import AuditEventInput
from superboss.modules.audit.service import AuditService
from superboss.modules.files.models import (
    File,
    FileLifecycleOutbox,
    FileState,
    FileStorageCleanup,
    FileUploadLifecycle,
    Upload,
)
from superboss.modules.files.schemas import UploadStart
from superboss.modules.files.storage import CompletedPart, ObjectStorage
from superboss.modules.users.models import Role


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


class FileScanService:
    """Atomically scan one quarantined object under its PostgreSQL File lock."""

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
        async with self.session_factory() as session, session.begin():
            file = await session.scalar(
                select(File).where(File.id == file_id).with_for_update()
            )
            if file is None or file.state in self._TERMINAL_STATES:
                return
            if file.state != FileState.QUARANTINED:
                return

            file.state = FileState.SCANNING
            await session.flush()
            digest = hashlib.sha256()

            async def hashing_chunks() -> AsyncGenerator[bytes]:
                async for chunk in self.storage.stream(file.object_key):
                    digest.update(chunk)
                    yield chunk

            chunks = hashing_chunks()
            try:
                try:
                    verdict = await self.scanner.scan(chunks)
                finally:
                    await chunks.aclose()
            except Exception:  # noqa: BLE001 -- persist only the fixed safe result
                file.state = FileState.FAILED
                file.scan_result = "SCAN_FAILED"
                return

            if digest.hexdigest() != file.sha256:
                file.state = FileState.FAILED
                file.scan_result = "HASH_MISMATCH"
            elif verdict.status == ScanStatus.CLEAN:
                file.state = FileState.CLEAN
                file.scan_result = "CLEAN"
            elif verdict.status == ScanStatus.INFECTED:
                file.state = FileState.INFECTED
                file.scan_result = self._safe_signature(verdict.signature)
            else:
                file.state = FileState.FAILED
                file.scan_result = "SCAN_FAILED"


class FileService:
    _COMPLETION_EXTERNAL_TIMEOUT_SECONDS = 20
    _COMPLETION_AMBIGUITY_GRACE_SECONDS = 120
    _PROVISION_EXTERNAL_TIMEOUT_SECONDS = 20
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
        fresh_file = await self.session.scalar(
            select(File)
            .where(File.id == upload.file_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            fresh_file is None
            or fresh_file.state != FileState.UPLOADING
            or fresh_file.project_id != upload.project_id
        ):
            await self.session.rollback()
            raise FileProvisioningPendingError()
        fresh_upload = await self.session.get(Upload, upload.id, populate_existing=True)
        if (
            fresh_upload is None
            or fresh_upload.file_id != fresh_file.id
            or fresh_upload.project_id != fresh_file.project_id
        ):
            await self.session.rollback()
            raise FileProvisioningPendingError()
        lifecycle = await self.session.scalar(
            select(FileUploadLifecycle)
            .where(FileUploadLifecycle.upload_id == fresh_upload.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            lifecycle is None
            or lifecycle.file_id != fresh_file.id
            or lifecycle.project_id != fresh_file.project_id
            or lifecycle.object_key != fresh_file.object_key
        ):
            await self.session.rollback()
            raise FileProvisioningPendingError()
        if lifecycle.provision_state == "READY":
            if (
                fresh_upload.multipart_id is not None
                and lifecycle.multipart_id == fresh_upload.multipart_id
            ):
                return fresh_upload
            await self.session.rollback()
            raise FileProvisioningPendingError()
        if lifecycle.provision_state != "PROVISIONING":
            await self.session.rollback()
            raise FileProvisioningPendingError()
        try:
            multipart_ids = await asyncio.wait_for(
                self._storage().list_multipart_uploads(lifecycle.object_key),
                timeout=self._PROVISION_EXTERNAL_TIMEOUT_SECONDS,
            )
        except Exception as error:
            await self.session.rollback()
            raise FileProvisioningPendingError() from error
        if len(multipart_ids) > 1:
            for multipart_id in multipart_ids:
                await self._record_multipart_cleanup(lifecycle, multipart_id)
            await self.session.commit()
            raise FileProvisioningPendingError()
        if len(multipart_ids) == 1:
            return await self._bind_multipart(fresh_upload, lifecycle, multipart_ids[0])
        try:
            multipart_id = await asyncio.wait_for(
                self._storage().create_multipart(lifecycle.object_key, lifecycle.content_type),
                timeout=self._PROVISION_EXTERNAL_TIMEOUT_SECONDS,
            )
        except Exception as error:
            await self.session.rollback()
            raise FileProvisioningPendingError() from error
        return await self._bind_multipart(fresh_upload, lifecycle, multipart_id)

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
        return await self._storage().presign_upload_part(
            file.object_key, upload.multipart_id, part_number, 900
        )

    async def complete_upload(
        self,
        actor: Actor,
        upload_id: UUID,
        parts: list[CompletedPart],
        request_id: UUID | None = None,
    ) -> File:
        just_prepared = False
        upload = await self.session.get(Upload, upload_id)
        if upload is None:
            raise FileUploadNotFoundError()
        file = await self.session.scalar(
            select(File)
            .where(File.id == upload.file_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if file is None:
            raise FileUploadNotActiveError()
        if file.project_id != upload.project_id:
            raise FileUploadProjectMismatchError()
        lifecycle = await self.session.scalar(
            select(FileUploadLifecycle)
            .where(FileUploadLifecycle.upload_id == upload_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if lifecycle is None or lifecycle.provision_state != "READY":
            raise FileUploadNotActiveError()
        require_project_access(actor, file.project_id)
        if upload.multipart_id is None:
            raise FileUploadNotActiveError()
        if len({p.part_number for p in parts}) != len(parts):
            raise ValueError("duplicate part number")
        canonical = sorted(parts, key=lambda p: p.part_number)
        encoded_parts = [{"part_number": p.part_number, "etag": p.etag} for p in canonical]
        digest = hashlib.sha256(
            json.dumps(encoded_parts, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()
        if lifecycle.completion_state == "QUARANTINED":
            if lifecycle.parts_digest == digest and file.state == FileState.QUARANTINED:
                return file
            raise FileUploadConflictError()
        if lifecycle.completion_state == "NONE":
            if file.state != FileState.UPLOADING:
                raise FileUploadNotActiveError()
            lifecycle.completion_state = "PREPARED"
            lifecycle.parts_digest = digest
            lifecycle.canonical_parts_json = encoded_parts
            lifecycle.completion_actor_kind = actor.kind
            lifecycle.completion_actor_id = actor.subject_id
            lifecycle.completion_actor_role = actor.role.value if actor.role is not None else None
            lifecycle.completion_request_id = request_id
            lifecycle.prepared_at = datetime.now(UTC)
            lifecycle.completion_event_key = uuid5(
                NAMESPACE_URL, f"file-complete:{upload_id}:{digest}"
            )
            lifecycle.completion_attempt_count = 1
            lifecycle.completion_next_attempt_at = datetime.now(UTC) + timedelta(
                seconds=self._COMPLETION_AMBIGUITY_GRACE_SECONDS
            )
            await self.session.commit()
            just_prepared = True
        elif lifecycle.parts_digest != digest:
            raise FileUploadConflictError()
        now = datetime.now(UTC)
        if (
            not just_prepared
            and
            lifecycle.completion_next_attempt_at is not None
            and lifecycle.completion_next_attempt_at > now
        ):
            raise FileCompletionPendingError()
        upload, file, lifecycle = await self._fresh_active_completion_context(upload_id)
        assert upload.multipart_id is not None
        within_ambiguity_grace = (
            not just_prepared
            and lifecycle.completion_attempt_count > 0
            and lifecycle.prepared_at is not None
            and datetime.now(UTC) - lifecycle.prepared_at
            < timedelta(seconds=self._COMPLETION_AMBIGUITY_GRACE_SECONDS)
        )
        try:
            metadata = await asyncio.wait_for(
                self._storage().stat_object(file.object_key),
                timeout=self._COMPLETION_EXTERNAL_TIMEOUT_SECONDS,
            )
        except Exception as error:
            await self.session.rollback()
            raise FileCompletionPendingError() from error
        if metadata is None:
            if within_ambiguity_grace:
                await self._mark_completion_ambiguous(lifecycle)
                raise FileCompletionPendingError()
            try:
                if not just_prepared:
                    lifecycle.completion_attempt_count += 1
                    lifecycle.completion_next_attempt_at = datetime.now(UTC) + timedelta(
                        seconds=self._COMPLETION_AMBIGUITY_GRACE_SECONDS
                    )
                    await self.session.commit()
                    upload, file, lifecycle = await self._fresh_active_completion_context(
                        upload_id
                    )
                    assert upload.multipart_id is not None
                multipart_id = upload.multipart_id
                assert multipart_id is not None
                metadata = await asyncio.wait_for(
                    self._storage().complete_multipart(
                        file.object_key, multipart_id, canonical
                    ),
                    timeout=self._COMPLETION_EXTERNAL_TIMEOUT_SECONDS,
                )
            except Exception:  # noqa: BLE001 -- a post-complete stat distinguishes ambiguity
                try:
                    metadata = await asyncio.wait_for(
                        self._storage().stat_object(file.object_key),
                        timeout=self._COMPLETION_EXTERNAL_TIMEOUT_SECONDS,
                    )
                except Exception:  # noqa: BLE001 -- ambiguous provider result is intentionally retried
                    metadata = None
                if metadata is None:
                    await self._mark_completion_ambiguous(lifecycle)
                    raise FileCompletionPendingError()
        if metadata is None:
            raise FileCompletionPendingError()
        if metadata.size_bytes != file.size_bytes:
            await self._fail_upload(file, upload, lifecycle)
            raise FileUploadSizeMismatchError()
        return await self._finalize_completion(file, lifecycle)

    async def _fresh_active_completion_context(
        self, upload_id: UUID
    ) -> tuple[Upload, File, FileUploadLifecycle]:
        """Re-lock current completion rows after a durable attempt commit."""
        upload = await self.session.get(Upload, upload_id, populate_existing=True)
        if upload is None:
            raise FileUploadNotActiveError()
        file = await self.session.scalar(
            select(File)
            .where(File.id == upload.file_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            file is None
            or file.project_id != upload.project_id
            or file.state != FileState.UPLOADING
            or upload.multipart_id is None
        ):
            raise FileUploadNotActiveError()
        lifecycle = await self.session.scalar(
            select(FileUploadLifecycle)
            .where(FileUploadLifecycle.upload_id == upload_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            lifecycle is None
            or lifecycle.provision_state != "READY"
            or lifecycle.completion_state != "PREPARED"
        ):
            raise FileUploadNotActiveError()
        return upload, file, lifecycle

    async def _mark_completion_ambiguous(self, lifecycle: FileUploadLifecycle) -> None:
        lifecycle.completion_state = "PREPARED"
        lifecycle.completion_last_error_code = "COMPLETION_AMBIGUOUS"
        lifecycle.completion_next_attempt_at = datetime.now(UTC) + timedelta(
            seconds=self._COMPLETION_AMBIGUITY_GRACE_SECONDS
        )
        await self.session.commit()

    async def _finalize_completion(
        self,
        file: File,
        lifecycle: FileUploadLifecycle,
    ) -> File:
        file_id = file.id
        file.state = FileState.QUARANTINED
        lifecycle.completion_state = "QUARANTINED"
        event_key = lifecycle.completion_event_key
        assert event_key is not None
        for kind in ("scan_dispatch", "completion_audit"):
            self.session.add(
                FileLifecycleOutbox(
                    id=uuid4(),
                    kind=kind,
                    dedupe_key=event_key,
                    file_id=file.id,
                    project_id=file.project_id,
                )
            )
        try:
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()
            replayed = await self.session.get(File, file_id)
            if replayed is not None and replayed.state == FileState.QUARANTINED:
                return replayed
            raise
        return file

    async def _record_cleanup(
        self,
        lifecycle: FileUploadLifecycle,
        operation: str,
        multipart_id: str | None,
    ) -> None:
        dedupe_key = hashlib.sha256(
            f"{operation}\x1f{lifecycle.object_key}\x1f{multipart_id or ''}".encode()
        ).hexdigest()
        existing = await self.session.scalar(
            select(FileStorageCleanup).where(
                FileStorageCleanup.operation == operation,
                FileStorageCleanup.dedupe_key == dedupe_key,
            )
        )
        if existing is None:
            self.session.add(
                FileStorageCleanup(
                    operation=operation,
                    dedupe_key=dedupe_key,
                    object_key=lifecycle.object_key,
                    multipart_id=multipart_id,
                    lifecycle_id=lifecycle.upload_id,
                )
            )

    async def _fail_upload(
        self, file: File, upload: Upload, lifecycle: FileUploadLifecycle
    ) -> None:
        lifecycle.completion_state = "COMPENSATION_PENDING"
        await self._record_cleanup(lifecycle, "DELETE_OBJECT", None)
        await self._record_cleanup(lifecycle, "ABORT_MULTIPART", upload.multipart_id)
        file.state = FileState.FAILED
        await self.session.commit()
        await self._best_effort_cleanup(lifecycle.upload_id)

    async def _best_effort_cleanup(self, lifecycle_id: UUID) -> None:
        bind = self.session.bind
        if bind is None:
            return
        session_factory = async_sessionmaker(bind, expire_on_commit=False)
        await FileLifecycleService(
            session_factory,
            self._storage(),
            self.enqueue_scan,
        ).reconcile_cleanup(limit=100, lifecycle_id=lifecycle_id)


class FileLifecycleService:
    """Claims durable storage compensation work without process-local coordination."""

    _CLEANUP_EXTERNAL_TIMEOUT_SECONDS = 20

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        storage: ObjectStorage,
        enqueue_scan: Callable[[UUID, UUID], Awaitable[None] | None] | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.storage = storage
        self.enqueue_scan = enqueue_scan

    async def deliver_completion(self, upload_id: UUID) -> bool:
        """Deliver immutable completion evidence before the idempotent scan job."""
        for kind in ("completion_audit", "scan_dispatch"):
            async with self.session_factory() as session:
                lifecycle = await session.scalar(
                    select(FileUploadLifecycle)
                    .where(FileUploadLifecycle.upload_id == upload_id)
                    .with_for_update(skip_locked=True)
                )
                if lifecycle is None or lifecycle.completion_event_key is None:
                    return False
                outbox = await session.scalar(
                    select(FileLifecycleOutbox)
                    .where(
                        FileLifecycleOutbox.kind == kind,
                        FileLifecycleOutbox.dedupe_key == lifecycle.completion_event_key,
                    )
                    .with_for_update(skip_locked=True)
                )
                if outbox is None:
                    return False
                if outbox.state == "DELIVERED":
                    continue
                now = datetime.now(UTC)
                expired = now - timedelta(seconds=30)
                if not (
                    (outbox.state == "PENDING" and outbox.next_attempt_at <= now)
                    or (
                        outbox.state == "DELIVERING"
                        and outbox.locked_at is not None
                        and outbox.locked_at <= expired
                    )
                ):
                    return False
                claim_token = uuid4()
                outbox.state = "DELIVERING"
                outbox.locked_at = now
                outbox.claim_token = claim_token
                outbox.attempt_count += 1
                await session.commit()
                try:
                    if kind == "completion_audit":
                        await self._record_completion_audit(lifecycle)
                    else:
                        await self._enqueue_scan(lifecycle)
                except Exception:  # noqa: BLE001 -- provider details are never persisted
                    await session.execute(
                        update(FileLifecycleOutbox)
                        .where(
                            FileLifecycleOutbox.id == outbox.id,
                            FileLifecycleOutbox.claim_token == claim_token,
                            FileLifecycleOutbox.state == "DELIVERING",
                        )
                        .values(
                            state="PENDING",
                            claim_token=None,
                            locked_at=None,
                            next_attempt_at=datetime.now(UTC) + timedelta(seconds=30),
                            last_error_code="AUDIT_FAILED"
                            if kind == "completion_audit"
                            else "DISPATCH_FAILED",
                        )
                    )
                    await session.commit()
                    return False
                await session.execute(
                    update(FileLifecycleOutbox)
                    .where(
                        FileLifecycleOutbox.id == outbox.id,
                        FileLifecycleOutbox.claim_token == claim_token,
                        FileLifecycleOutbox.state == "DELIVERING",
                    )
                    .values(
                        state="DELIVERED", claim_token=None, locked_at=None, last_error_code=None
                    )
                )
                await session.commit()
        return True

    async def deliver_due(self, limit: int = 100) -> int:
        """Drain due completion deliveries; the return value counts fully delivered files."""
        now = datetime.now(UTC)
        expired = now - timedelta(seconds=30)
        async with self.session_factory() as session:
            outboxes = list(
                await session.scalars(
                    select(FileLifecycleOutbox)
                    .where(
                        or_(
                            and_(
                                FileLifecycleOutbox.state == "PENDING",
                                FileLifecycleOutbox.next_attempt_at <= now,
                            ),
                            and_(
                                FileLifecycleOutbox.state == "DELIVERING",
                                FileLifecycleOutbox.locked_at.is_not(None),
                                FileLifecycleOutbox.locked_at <= expired,
                            ),
                        )
                    )
                    .order_by(
                        case((FileLifecycleOutbox.kind == "completion_audit", 0), else_=1),
                        FileLifecycleOutbox.next_attempt_at,
                    )
                    .limit(limit)
                )
            )
            candidates = [(outbox.file_id, outbox.dedupe_key) for outbox in outboxes]

        delivered = 0
        seen_files: set[UUID] = set()
        for file_id, event_key in candidates:
            if file_id in seen_files:
                continue
            seen_files.add(file_id)
            async with self.session_factory() as session:
                lifecycle = await session.scalar(
                    select(FileUploadLifecycle).where(
                        FileUploadLifecycle.file_id == file_id,
                        FileUploadLifecycle.completion_event_key == event_key,
                    )
                )
                if lifecycle is None:
                    continue
                audit = await session.scalar(
                    select(FileLifecycleOutbox).where(
                        FileLifecycleOutbox.kind == "completion_audit",
                        FileLifecycleOutbox.dedupe_key == event_key,
                    )
                )
                if audit is None or audit.state not in {"PENDING", "DELIVERED"}:
                    continue
                upload_id = lifecycle.upload_id
            if await self.deliver_completion(upload_id):
                delivered += 1
        return delivered

    async def _record_completion_audit(self, lifecycle: FileUploadLifecycle) -> None:
        if (
            lifecycle.completion_actor_id is None
            or lifecycle.completion_actor_role is None
            or lifecycle.completion_request_id is None
            or lifecycle.completion_event_key is None
        ):
            raise RuntimeError("completion audit snapshot is incomplete")
        actor_kind = lifecycle.completion_actor_kind
        if actor_kind not in {"user", "device", "system"}:
            raise RuntimeError("completion actor snapshot is invalid")
        role = Role(lifecycle.completion_actor_role)
        if actor_kind == "user":
            actor = Actor("user", lifecycle.completion_actor_id, role, frozenset(), frozenset())
        elif actor_kind == "device":
            actor = Actor("device", lifecycle.completion_actor_id, role, frozenset(), frozenset())
        else:
            actor = Actor("system", lifecycle.completion_actor_id, role, frozenset(), frozenset())
        await AuditService(self.session_factory).record(
            AuditEventInput(
                actor=actor,
                action="file.upload.complete",
                object_type="file",
                object_id=lifecycle.file_id,
                project_id=lifecycle.project_id,
                outcome="SUCCESS",
                request_id=lifecycle.completion_request_id,
                event_key=lifecycle.completion_event_key,
                metadata={"state": "QUARANTINED", "size_bytes": lifecycle.declared_size_bytes},
            )
        )

    async def _enqueue_scan(self, lifecycle: FileUploadLifecycle) -> None:
        if self.enqueue_scan is None or lifecycle.completion_event_key is None:
            raise RuntimeError("file scan dispatcher is not configured")
        result = self.enqueue_scan(lifecycle.file_id, lifecycle.completion_event_key)
        if isinstance(result, Awaitable):
            await result

    async def reconcile(self, limit: int = 100) -> int:
        """Run bounded compensation jobs; failed providers remain safely retryable."""
        completed = await self.reconcile_cleanup(limit)
        async with self.session_factory() as session:
            provisioning_ids = list(
                await session.scalars(
                    select(FileUploadLifecycle.upload_id)
                    .where(FileUploadLifecycle.provision_state == "PROVISIONING")
                    .limit(limit)
                )
            )
        for upload_id in provisioning_ids:
            async with self.session_factory() as session:
                upload = await session.get(Upload, upload_id)
                if upload is None:
                    continue
                file = await session.get(File, upload.file_id)
                if file is None:
                    continue
                try:
                    await FileService(session, self.storage)._provision_upload(upload, file)
                except FileProvisioningPendingError:
                    continue
                completed += 1
        async with self.session_factory() as session:
            now = datetime.now(UTC)
            lifecycles = list(
                await session.scalars(
                    select(FileUploadLifecycle)
                    .where(
                        FileUploadLifecycle.completion_state == "PREPARED",
                        or_(
                            FileUploadLifecycle.completion_next_attempt_at.is_(None),
                            FileUploadLifecycle.completion_next_attempt_at <= now,
                        ),
                    )
                    # Lock order is File then lifecycle. This is only an unclaimed
                    # candidate; final state is rechecked under those row locks.
                    .limit(1)
                )
            )
            for candidate in lifecycles:
                try:
                    upload, file, lifecycle = await FileService(
                        session, self.storage
                    )._fresh_active_completion_context(candidate.upload_id)
                except FileUploadNotActiveError:
                    continue
                if (
                    lifecycle.completion_next_attempt_at is not None
                    and lifecycle.completion_next_attempt_at > now
                ):
                    continue
                grace_until = (
                    lifecycle.prepared_at
                    + timedelta(seconds=FileService._COMPLETION_AMBIGUITY_GRACE_SECONDS)
                    if lifecycle.prepared_at is not None
                    else now
                )
                if now < grace_until:
                    lifecycle.completion_next_attempt_at = max(
                        lifecycle.completion_next_attempt_at or grace_until, grace_until
                    )
                    await session.commit()
                    continue
                lifecycle.completion_attempt_count += 1
                lifecycle.completion_next_attempt_at = now + timedelta(
                    seconds=FileService._COMPLETION_AMBIGUITY_GRACE_SECONDS
                )
                await session.commit()
                try:
                    upload, file, lifecycle = await FileService(
                        session, self.storage
                    )._fresh_active_completion_context(candidate.upload_id)
                except FileUploadNotActiveError:
                    continue
                try:
                    metadata = await asyncio.wait_for(
                        self.storage.stat_object(lifecycle.object_key),
                        timeout=FileService._COMPLETION_EXTERNAL_TIMEOUT_SECONDS,
                    )
                    if metadata is None:
                        parts: list[CompletedPart] = []
                        for item in lifecycle.canonical_parts_json or []:
                            if not isinstance(item, dict):
                                continue
                            part_number = item.get("part_number")
                            etag = item.get("etag")
                            if (
                                not isinstance(part_number, int)
                                or isinstance(part_number, bool)
                                or not isinstance(etag, str)
                            ):
                                continue
                            parts.append(CompletedPart(part_number, etag))
                        if not parts:
                            continue
                        multipart_id = upload.multipart_id
                        assert multipart_id is not None
                        metadata = await asyncio.wait_for(
                            self.storage.complete_multipart(
                                lifecycle.object_key, multipart_id, parts
                            ),
                            timeout=FileService._COMPLETION_EXTERNAL_TIMEOUT_SECONDS,
                        )
                except Exception:  # noqa: BLE001
                    await FileService(session, self.storage)._mark_completion_ambiguous(lifecycle)
                    continue
                if metadata is None:
                    continue
                if metadata.size_bytes != lifecycle.declared_size_bytes:
                    await FileService(session, self.storage)._fail_upload(file, upload, lifecycle)
                    completed += 1
                    continue
                await FileService(session, self.storage)._finalize_completion(file, lifecycle)
                completed += 1
        completed += await self.deliver_due(limit)
        return completed

    async def reconcile_cleanup(self, limit: int = 100, lifecycle_id: UUID | None = None) -> int:
        """Claim and execute due cleanup work with a per-claim lease token."""
        completed = 0
        async with self.session_factory() as session:
            cleanup_filters = [
                or_(
                    and_(
                        FileStorageCleanup.state == "PENDING",
                        FileStorageCleanup.next_attempt_at <= datetime.now(UTC),
                    ),
                    and_(
                        FileStorageCleanup.state == "RUNNING",
                        FileStorageCleanup.locked_at <= datetime.now(UTC) - timedelta(seconds=30),
                    ),
                )
            ]
            if lifecycle_id is not None:
                cleanup_filters.append(FileStorageCleanup.lifecycle_id == lifecycle_id)
            cleanups = list(
                await session.scalars(
                    select(FileStorageCleanup)
                    .where(*cleanup_filters)
                    .order_by(
                        case(
                            (FileStorageCleanup.operation == "ABORT_MULTIPART", 0),
                            (FileStorageCleanup.operation == "DISCOVER_MULTIPART", 1),
                            else_=2,
                        ),
                        FileStorageCleanup.next_attempt_at,
                    )
                    .with_for_update(skip_locked=True)
                    .limit(limit)
                )
            )
            claimed_cleanups: list[tuple[UUID, UUID, str, str, str | None]] = []
            for cleanup in cleanups:
                cleanup.state = "RUNNING"
                cleanup.locked_at = datetime.now(UTC)
                claim_token = uuid4()
                cleanup.claim_token = claim_token
                cleanup.attempt_count += 1
                claimed_cleanups.append(
                    (
                        cleanup.id,
                        claim_token,
                        cleanup.operation,
                        cleanup.object_key,
                        cleanup.multipart_id,
                    )
                )
            if claimed_cleanups:
                await session.commit()
            for cleanup_id, claim_token, operation, object_key, multipart_id in claimed_cleanups:
                try:
                    if operation == "DELETE_OBJECT":
                        await asyncio.wait_for(
                            self.storage.delete_object(object_key),
                            timeout=self._CLEANUP_EXTERNAL_TIMEOUT_SECONDS,
                        )
                    elif operation == "DISCOVER_MULTIPART":
                        multipart_ids = await asyncio.wait_for(
                            self.storage.list_multipart_uploads(object_key),
                            timeout=self._CLEANUP_EXTERNAL_TIMEOUT_SECONDS,
                        )
                        for discovered_id in sorted(multipart_ids):
                            await asyncio.wait_for(
                                self.storage.abort_multipart(object_key, discovered_id),
                                timeout=self._CLEANUP_EXTERNAL_TIMEOUT_SECONDS,
                            )
                    elif multipart_id is not None:
                        await asyncio.wait_for(
                            self.storage.abort_multipart(object_key, multipart_id),
                            timeout=self._CLEANUP_EXTERNAL_TIMEOUT_SECONDS,
                        )
                except Exception:  # noqa: BLE001 -- no provider data enters durable state
                    await session.execute(
                        update(FileStorageCleanup)
                        .where(
                            FileStorageCleanup.id == cleanup_id,
                            FileStorageCleanup.claim_token == claim_token,
                            FileStorageCleanup.state == "RUNNING",
                        )
                        .values(
                            state="PENDING",
                            claim_token=None,
                            locked_at=None,
                            next_attempt_at=datetime.now(UTC) + timedelta(seconds=30),
                            last_error_code=(
                                "DELETE_FAILED"
                                if operation == "DELETE_OBJECT"
                                else "DISCOVERY_FAILED"
                                if operation == "DISCOVER_MULTIPART"
                                else "ABORT_FAILED"
                            ),
                        )
                    )
                else:
                    claimed_id = await session.scalar(
                        update(FileStorageCleanup)
                        .where(
                            FileStorageCleanup.id == cleanup_id,
                            FileStorageCleanup.claim_token == claim_token,
                            FileStorageCleanup.state == "RUNNING",
                        )
                        .values(
                            state="DONE", claim_token=None, locked_at=None, last_error_code=None
                        )
                        .returning(FileStorageCleanup.id)
                    )
                    if claimed_id is not None:
                        completed += 1
                await session.commit()
        return completed
