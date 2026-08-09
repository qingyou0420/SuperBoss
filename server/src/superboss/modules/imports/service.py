"""Creation and idempotent provisioning of normalized K3 import jobs."""

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import and_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from superboss.core.actors import Actor
from superboss.core.errors import DomainError, ForbiddenError
from superboss.modules.audit.models import AuditLog
from superboss.modules.audit.schemas import AuditEventInput
from superboss.modules.audit.service import AuditService
from superboss.modules.files.models import File, FileState, Upload
from superboss.modules.files.schemas import UploadStart
from superboss.modules.files.service import FileService, FileUploadConflictError
from superboss.modules.files.storage import CompletedPart, ObjectStorage
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


class ImportAttachmentNotFoundError(DomainError):
    def __init__(self) -> None:
        super().__init__("IMPORT_ATTACHMENT_NOT_FOUND", "Import attachment not found", 404)


class ImportJobNotFoundError(DomainError):
    def __init__(self) -> None:
        super().__init__("IMPORT_JOB_NOT_FOUND", "Import job not found", 404)


class ImportAttachmentsIncompleteError(DomainError):
    def __init__(self) -> None:
        super().__init__(
            "IMPORT_ATTACHMENTS_INCOMPLETE",
            "Import attachments are incomplete",
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
    result_code: str | None
    submitted_at: datetime | None
    attachments: tuple[ImportAttachmentResult, ...]


@dataclass(frozen=True)
class _AttachmentUploadTarget:
    project_id: UUID
    upload_id: UUID


@dataclass(frozen=True)
class _SubmitTarget:
    project_id: UUID


@dataclass(frozen=True)
class _FileObservation:
    kind: AttachmentKind
    state: FileState
    sha256: str


@dataclass(frozen=True)
class _ImportEvaluation:
    status: ImportStatus
    result_code: str | None


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

    async def presign_part(
        self,
        actor: Actor,
        job_id: UUID,
        attachment_id: UUID,
        part_number: int,
        *,
        request_id: UUID,
    ) -> str:
        target = await self._resolve_attachment_upload(
            actor,
            job_id,
            attachment_id,
            action="import.attachment.part_url",
            request_id=request_id,
        )
        async with self.session_factory() as file_session:
            return await FileService(file_session, self.storage).presign_import_part(
                actor,
                target.upload_id,
                part_number,
            )

    async def complete_attachment(
        self,
        actor: Actor,
        job_id: UUID,
        attachment_id: UUID,
        parts: list[CompletedPart],
        *,
        request_id: UUID,
    ) -> File:
        target = await self._resolve_attachment_upload(
            actor,
            job_id,
            attachment_id,
            action="import.attachment.complete",
            request_id=request_id,
        )
        async with self.session_factory() as file_session:
            return await FileService(file_session, self.storage).complete_import_upload(
                actor,
                target.upload_id,
                parts,
                request_id,
            )

    async def _resolve_attachment_upload(
        self,
        actor: Actor,
        job_id: UUID,
        attachment_id: UUID,
        *,
        action: str,
        request_id: UUID,
    ) -> _AttachmentUploadTarget:
        if (
            actor.kind != "device"
            or actor.role is not None
            or "imports:upload" not in actor.scopes
        ):
            error = ForbiddenError(
                "IMPORT_UPLOAD_FORBIDDEN",
                "Import upload is not permitted",
            )
            await self._record_attachment_denial(
                actor,
                job_id,
                attachment_id,
                project_id=None,
                action=action,
                request_id=request_id,
                error=error,
            )
            raise error

        async with self.session_factory() as session:
            row = (
                await session.execute(
                    select(
                        ImportJob.device_id,
                        ImportJob.project_id,
                        ImportAttachment.upload_id,
                    )
                    .select_from(ImportJob)
                    .join(
                        ImportAttachment,
                        and_(
                            ImportAttachment.job_id == ImportJob.id,
                            ImportAttachment.project_id == ImportJob.project_id,
                        ),
                    )
                    .join(
                        File,
                        and_(
                            File.id == ImportAttachment.file_id,
                            File.project_id == ImportAttachment.project_id,
                        ),
                    )
                    .join(
                        Upload,
                        and_(
                            Upload.id == ImportAttachment.upload_id,
                            Upload.file_id == ImportAttachment.file_id,
                            Upload.project_id == ImportAttachment.project_id,
                        ),
                    )
                    .where(
                        ImportJob.id == job_id,
                        ImportAttachment.id == attachment_id,
                    )
                )
            ).one_or_none()

        if row is None or row.device_id != actor.subject_id:
            not_found_error = ImportAttachmentNotFoundError()
            await self._record_attachment_denial(
                actor,
                job_id,
                attachment_id,
                project_id=None,
                action=action,
                request_id=request_id,
                error=not_found_error,
            )
            raise not_found_error
        if row.project_id not in actor.project_ids:
            error = ForbiddenError(
                "IMPORT_UPLOAD_FORBIDDEN",
                "Import upload is not permitted",
            )
            await self._record_attachment_denial(
                actor,
                job_id,
                attachment_id,
                project_id=row.project_id,
                action=action,
                request_id=request_id,
                error=error,
            )
            raise error
        return _AttachmentUploadTarget(
            project_id=row.project_id,
            upload_id=row.upload_id,
        )

    async def _record_attachment_denial(
        self,
        actor: Actor,
        job_id: UUID,
        attachment_id: UUID,
        *,
        project_id: UUID | None,
        action: str,
        request_id: UUID,
        error: DomainError,
    ) -> None:
        await AuditService(self.session_factory).record(
            AuditEventInput(
                actor=actor,
                action=action,
                object_type="import_attachment",
                object_id=attachment_id,
                project_id=project_id,
                outcome="DENIED",
                request_id=request_id,
                metadata={
                    "error_code": error.code,
                    "job_id": str(job_id),
                    "attachment_id": str(attachment_id),
                },
            )
        )

    async def submit(
        self,
        actor: Actor,
        job_id: UUID,
        *,
        request_id: UUID,
    ) -> ImportJobResult:
        target = await self._preflight_submit(actor, job_id, request_id)
        denial: DomainError | None = None
        denial_project_id: UUID | None = target.project_id
        result: ImportJobResult | None = None

        async with self.session_factory() as session, session.begin():
            job = await session.scalar(
                select(ImportJob).where(ImportJob.id == job_id).with_for_update()
            )
            if job is None or job.device_id != actor.subject_id:
                denial = ImportJobNotFoundError()
                denial_project_id = None
            elif job.project_id != target.project_id or job.project_id not in actor.project_ids:
                denial = ForbiddenError(
                    "IMPORT_SUBMIT_FORBIDDEN",
                    "Import submission is not permitted",
                )
                denial_project_id = job.project_id
            elif job.status in {
                ImportStatus.RECEIVED,
                ImportStatus.REJECTED,
                ImportStatus.CONFLICT,
            }:
                result = await self._job_result(session, job)
            else:
                observations = await self._file_observations(session, job.id)
                if job.status == ImportStatus.UPLOADING and any(
                    observation.state == FileState.UPLOADING
                    for observation in observations
                ):
                    denial = ImportAttachmentsIncompleteError()
                else:
                    evaluation = self._evaluate_files(job, observations)
                    if (
                        job.status == ImportStatus.SCANNING
                        and evaluation.status == ImportStatus.SCANNING
                    ):
                        result = await self._job_result(session, job)
                    else:
                        self._apply_transition(
                            session,
                            job,
                            evaluation,
                            action="import.submit",
                            actor_kind="device",
                            actor_id=actor.subject_id,
                            request_id=request_id,
                        )
                        result = await self._job_result(session, job)

        if denial is not None:
            await self._record_submit_denial(
                actor,
                job_id,
                project_id=denial_project_id,
                request_id=request_id,
                error=denial,
            )
            raise denial
        assert result is not None
        return result

    async def _preflight_submit(
        self,
        actor: Actor,
        job_id: UUID,
        request_id: UUID,
    ) -> _SubmitTarget:
        if (
            actor.kind != "device"
            or actor.role is not None
            or "imports:submit" not in actor.scopes
        ):
            forbidden = ForbiddenError(
                "IMPORT_SUBMIT_FORBIDDEN",
                "Import submission is not permitted",
            )
            await self._record_submit_denial(
                actor,
                job_id,
                project_id=None,
                request_id=request_id,
                error=forbidden,
            )
            raise forbidden

        async with self.session_factory() as session:
            row = (
                await session.execute(
                    select(ImportJob.device_id, ImportJob.project_id).where(
                        ImportJob.id == job_id
                    )
                )
            ).one_or_none()
        if row is None or row.device_id != actor.subject_id:
            not_found = ImportJobNotFoundError()
            await self._record_submit_denial(
                actor,
                job_id,
                project_id=None,
                request_id=request_id,
                error=not_found,
            )
            raise not_found
        if row.project_id not in actor.project_ids:
            forbidden = ForbiddenError(
                "IMPORT_SUBMIT_FORBIDDEN",
                "Import submission is not permitted",
            )
            await self._record_submit_denial(
                actor,
                job_id,
                project_id=row.project_id,
                request_id=request_id,
                error=forbidden,
            )
            raise forbidden
        return _SubmitTarget(project_id=row.project_id)

    async def _record_submit_denial(
        self,
        actor: Actor,
        job_id: UUID,
        *,
        project_id: UUID | None,
        request_id: UUID,
        error: DomainError,
    ) -> None:
        await AuditService(self.session_factory).record(
            AuditEventInput(
                actor=actor,
                action="import.submit",
                object_type="import_job",
                object_id=job_id,
                project_id=project_id,
                outcome="DENIED",
                request_id=request_id,
                metadata={"error_code": error.code, "job_id": str(job_id)},
            )
        )

    async def reconcile_file(self, file_id: UUID) -> None:
        async with self.session_factory() as session:
            job_ids = sorted(
                set(
                    (
                        await session.scalars(
                            select(ImportAttachment.job_id).where(
                                ImportAttachment.file_id == file_id
                            )
                        )
                    ).all()
                ),
                key=str,
            )
        if not job_ids:
            return

        async with self.session_factory() as session, session.begin():
            jobs = list(
                await session.scalars(
                    select(ImportJob)
                    .where(ImportJob.id.in_(job_ids))
                    .order_by(ImportJob.id)
                    .with_for_update()
                )
            )
            for job in jobs:
                if job.status != ImportStatus.SCANNING:
                    continue
                observations = await self._file_observations(session, job.id)
                evaluation = self._evaluate_files(job, observations)
                if evaluation.status == ImportStatus.SCANNING:
                    continue
                event_key = self._transition_event_key(
                    "import.reconcile", job.id, evaluation.status
                )
                self._apply_transition(
                    session,
                    job,
                    evaluation,
                    action="import.reconcile",
                    actor_kind="system",
                    actor_id=None,
                    request_id=event_key,
                )

    @staticmethod
    async def _file_observations(
        session: AsyncSession,
        job_id: UUID,
    ) -> list[_FileObservation]:
        rows = (
            await session.execute(
                select(ImportAttachment.kind, File.state, File.sha256)
                .select_from(ImportAttachment)
                .join(
                    File,
                    and_(
                        File.id == ImportAttachment.file_id,
                        File.project_id == ImportAttachment.project_id,
                    ),
                )
                .where(ImportAttachment.job_id == job_id)
                .order_by(ImportAttachment.kind, ImportAttachment.id)
            )
        ).all()
        return [
            _FileObservation(kind=row.kind, state=row.state, sha256=row.sha256)
            for row in rows
        ]

    @staticmethod
    def _evaluate_files(
        job: ImportJob,
        observations: list[_FileObservation],
    ) -> _ImportEvaluation:
        states = {observation.state for observation in observations}
        if FileState.INFECTED in states:
            return _ImportEvaluation(
                ImportStatus.REJECTED,
                "ATTACHMENT_INFECTED",
            )
        if FileState.FAILED in states:
            return _ImportEvaluation(
                ImportStatus.REJECTED,
                "ATTACHMENT_SCAN_FAILED",
            )
        if not observations or states != {FileState.CLEAN}:
            return _ImportEvaluation(ImportStatus.SCANNING, None)
        original = next(
            (
                observation
                for observation in observations
                if observation.kind == AttachmentKind.ORIGINAL
            ),
            None,
        )
        if (
            job.base_sha256 is not None
            and original is not None
            and original.sha256 != job.base_sha256
        ):
            return _ImportEvaluation(
                ImportStatus.CONFLICT,
                "BASE_SHA256_MISMATCH",
            )
        return _ImportEvaluation(ImportStatus.RECEIVED, None)

    @classmethod
    def _apply_transition(
        cls,
        session: AsyncSession,
        job: ImportJob,
        evaluation: _ImportEvaluation,
        *,
        action: str,
        actor_kind: str,
        actor_id: UUID | None,
        request_id: UUID,
    ) -> None:
        now = datetime.now(UTC)
        job.status = evaluation.status
        job.result_code = evaluation.result_code
        job.submitted_at = job.submitted_at or now
        job.updated_at = now
        event_key = cls._transition_event_key(action, job.id, evaluation.status)
        session.add(
            AuditLog(
                actor_kind=actor_kind,
                actor_id=actor_id,
                action=action,
                object_type="import_job",
                object_id=job.id,
                project_id=job.project_id,
                outcome="SUCCESS",
                metadata_json={
                    "actor_role": None,
                    "status": evaluation.status.value,
                    "result_code": evaluation.result_code,
                },
                request_id=request_id,
                event_key=event_key,
            )
        )

    @staticmethod
    def _transition_event_key(action: str, job_id: UUID, status: ImportStatus) -> UUID:
        return uuid5(
            NAMESPACE_URL,
            f"superboss:{action}:{job_id}:{status.value}",
        )

    async def _job_result(
        self,
        session: AsyncSession,
        job: ImportJob,
    ) -> ImportJobResult:
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
            result_code=job.result_code,
            submitted_at=job.submitted_at,
            attachments=tuple(self._attachment_result(row) for row in attachments),
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
                result_code=job.result_code,
                submitted_at=job.submitted_at,
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
            result_code=job.result_code,
            submitted_at=job.submitted_at,
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
