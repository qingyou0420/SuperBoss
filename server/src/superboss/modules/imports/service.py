"""Creation and idempotent provisioning of normalized K3 import jobs."""

import hashlib
import re
from dataclasses import dataclass
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from superboss.core.actors import Actor
from superboss.core.errors import DomainError, ForbiddenError
from superboss.modules.audit.models import AuditLog
from superboss.modules.audit.schemas import AuditEventInput
from superboss.modules.audit.service import AuditService
from superboss.modules.files.models import Upload
from superboss.modules.files.schemas import UploadStart
from superboss.modules.files.service import FileService, FileUploadConflictError
from superboss.modules.files.storage import ObjectStorage
from superboss.modules.imports.models import (
    AttachmentKind,
    ImportAttachment,
    ImportJob,
    ImportStatus,
)
from superboss.modules.imports.schemas import ImportJobCreate, canonical_manifest_bytes


class ImportIdempotencyConflictError(DomainError):
    def __init__(self) -> None:
        super().__init__(
            "IMPORT_IDEMPOTENCY_CONFLICT",
            "Idempotency key is already bound to another import manifest",
            409,
        )


@dataclass(frozen=True)
class ImportAttachmentResult:
    id: UUID
    file_id: UUID
    upload_id: UUID
    kind: AttachmentKind


@dataclass(frozen=True)
class ImportJobResult:
    id: UUID
    status: ImportStatus
    attachments: tuple[ImportAttachmentResult, ...]


class ImportService:
    """Create import jobs around the existing durable File/Upload lifecycle."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        storage: ObjectStorage,
    ) -> None:
        self.session_factory = session_factory
        self.storage = storage

    async def create(
        self,
        actor: Actor,
        command: ImportJobCreate,
        idempotency_key: str,
        *,
        request_id: UUID,
    ) -> ImportJobResult:
        self._validate_idempotency_key(idempotency_key)
        if not self._can_create(actor, command.project_id):
            await self._record_denial(actor, command.project_id, request_id)
            raise ForbiddenError("IMPORT_CREATE_FORBIDDEN", "Import creation is not permitted")

        manifest_bytes = canonical_manifest_bytes(command)
        manifest_fingerprint = hashlib.sha256(manifest_bytes).hexdigest()
        canonical_manifest = command.model_dump(mode="json")
        existing = await self._existing_result(
            actor.subject_id,
            idempotency_key,
            manifest_fingerprint,
        )
        if existing is not None:
            return existing

        uploads: list[Upload] = []
        for declaration in command.attachments:
            upload_command = UploadStart(
                project_id=command.project_id,
                filename=declaration.filename,
                size_bytes=declaration.size_bytes,
                sha256=declaration.sha256,
                category="kimi-imports",
                file_date=command.k3_result.processed_at.date(),
                content_type=declaration.content_type,
            )
            try:
                async with self.session_factory() as file_session:
                    upload = await FileService(
                        file_session,
                        self.storage,
                    ).start_import_upload(
                        actor,
                        upload_command,
                        self._child_idempotency_key(
                            actor.subject_id,
                            idempotency_key,
                            declaration.kind,
                        ),
                    )
            except FileUploadConflictError as error:
                raise ImportIdempotencyConflictError() from error
            uploads.append(upload)

        return await self._persist_job(
            actor,
            command,
            idempotency_key,
            canonical_manifest,
            manifest_fingerprint,
            uploads,
            request_id,
        )

    @staticmethod
    def _validate_idempotency_key(idempotency_key: str) -> None:
        if not re.fullmatch(r"[!-~]{1,255}", idempotency_key):
            raise ValueError("invalid Idempotency-Key")

    @staticmethod
    def _can_create(actor: Actor, project_id: UUID) -> bool:
        return (
            actor.kind == "device"
            and actor.role is None
            and project_id in actor.project_ids
            and "imports:create" in actor.scopes
        )

    async def _record_denial(
        self,
        actor: Actor,
        project_id: UUID,
        request_id: UUID,
    ) -> None:
        await AuditService(self.session_factory).record(
            AuditEventInput(
                actor=actor,
                action="import.create",
                object_type="import_job",
                object_id=None,
                project_id=project_id,
                outcome="DENIED",
                request_id=request_id,
                metadata={"reason": "IMPORT_CREATE_FORBIDDEN"},
            )
        )

    async def _existing_result(
        self,
        device_id: UUID,
        idempotency_key: str,
        manifest_fingerprint: str,
    ) -> ImportJobResult | None:
        async with self.session_factory() as session:
            existing = await session.scalar(
                select(ImportJob).where(
                    ImportJob.device_id == device_id,
                    ImportJob.idempotency_key == idempotency_key,
                )
            )
            if existing is None:
                return None
            return await self._matching_result(session, existing, manifest_fingerprint)

    async def _persist_job(
        self,
        actor: Actor,
        command: ImportJobCreate,
        idempotency_key: str,
        canonical_manifest: dict[str, object],
        manifest_fingerprint: str,
        uploads: list[Upload],
        request_id: UUID,
    ) -> ImportJobResult:
        async with self.session_factory() as session:
            existing = await session.scalar(
                select(ImportJob).where(
                    ImportJob.device_id == actor.subject_id,
                    ImportJob.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                return await self._matching_result(session, existing, manifest_fingerprint)

            job = ImportJob(
                id=uuid4(),
                device_id=actor.subject_id,
                project_id=command.project_id,
                idempotency_key=idempotency_key,
                local_task_id=command.local_task_id,
                external_document_reference=command.external_document_reference,
                base_sha256=command.base_sha256,
                canonical_manifest_json=canonical_manifest,
                manifest_fingerprint=manifest_fingerprint,
                status=ImportStatus.UPLOADING,
                result_code=None,
                submitted_at=None,
            )
            session.add(job)
            try:
                await session.flush()
            except IntegrityError as error:
                is_idempotency = self._constraint_name(error) == (
                    "uq_import_jobs_device_idempotency"
                )
                await session.rollback()
                if not is_idempotency:
                    raise
                winner = await session.scalar(
                    select(ImportJob).where(
                        ImportJob.device_id == actor.subject_id,
                        ImportJob.idempotency_key == idempotency_key,
                    )
                )
                if winner is None:
                    raise
                return await self._matching_result(session, winner, manifest_fingerprint)

            attachment_rows = [
                ImportAttachment(
                    job_id=job.id,
                    project_id=job.project_id,
                    file_id=upload.file_id,
                    upload_id=upload.id,
                    kind=declaration.kind,
                )
                for declaration, upload in zip(command.attachments, uploads, strict=True)
            ]
            session.add_all(attachment_rows)
            session.add(
                AuditLog(
                    actor_kind=actor.kind,
                    actor_id=actor.subject_id,
                    action="import.create",
                    object_type="import_job",
                    object_id=job.id,
                    project_id=job.project_id,
                    outcome="SUCCESS",
                    metadata_json={
                        "actor_role": None,
                        "attachment_count": len(attachment_rows),
                        "attachment_kinds": [row.kind.value for row in attachment_rows],
                        "manifest_fingerprint": manifest_fingerprint,
                        "status": ImportStatus.UPLOADING.value,
                    },
                    request_id=request_id,
                    event_key=uuid5(NAMESPACE_URL, f"superboss:import-create:{job.id}"),
                )
            )
            await session.commit()
            return ImportJobResult(
                id=job.id,
                status=job.status,
                attachments=tuple(self._attachment_result(row) for row in attachment_rows),
            )

    async def _matching_result(
        self,
        session: AsyncSession,
        job: ImportJob,
        manifest_fingerprint: str,
    ) -> ImportJobResult:
        if job.manifest_fingerprint != manifest_fingerprint:
            raise ImportIdempotencyConflictError()
        attachments = list(
            await session.scalars(
                select(ImportAttachment)
                .where(ImportAttachment.job_id == job.id)
                .order_by(ImportAttachment.kind, ImportAttachment.id)
            )
        )
        return ImportJobResult(
            id=job.id,
            status=job.status,
            attachments=tuple(self._attachment_result(row) for row in attachments),
        )

    @staticmethod
    def _attachment_result(attachment: ImportAttachment) -> ImportAttachmentResult:
        return ImportAttachmentResult(
            id=attachment.id,
            file_id=attachment.file_id,
            upload_id=attachment.upload_id,
            kind=attachment.kind,
        )

    @staticmethod
    def _child_idempotency_key(
        device_id: UUID,
        idempotency_key: str,
        kind: AttachmentKind,
    ) -> str:
        material = f"{device_id}\x1f{idempotency_key}\x1f{kind.value}".encode()
        return f"import-{hashlib.sha256(material).hexdigest()}"

    @staticmethod
    def _constraint_name(error: IntegrityError) -> str | None:
        cause = getattr(error.orig, "__cause__", None)
        return getattr(cause, "constraint_name", None)
