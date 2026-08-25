"""Creation and idempotent provisioning of normalized K3 import jobs."""

import hashlib
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import and_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from superboss.core.actors import Actor
from superboss.core.errors import ConflictError, DomainError, ForbiddenError, NotFoundError
from superboss.modules.audit.models import AuditLog
from superboss.modules.audit.schemas import AuditEventInput
from superboss.modules.audit.service import AuditService
from superboss.modules.files.models import File, FileState
from superboss.modules.files.schemas import UploadStart
from superboss.modules.files.service import FileService
from superboss.modules.files.storage import CompletedPart, ObjectStorage
from superboss.modules.imports.models import (
    AttachmentKind,
    ImportAttachment,
    ImportJob,
    ImportStatus,
)
from superboss.modules.imports.schemas import ImportJobCreate, K3Result, canonical_manifest_bytes
from superboss.modules.projects.models import Project
from superboss.modules.users.models import Role


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
    updated_at: datetime
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


@dataclass(frozen=True)
class ImportAttachmentView:
    id: UUID
    file_id: UUID
    upload_id: UUID
    kind: AttachmentKind
    file_state: FileState


@dataclass(frozen=True)
class ImportJobView:
    id: UUID
    project_id: UUID
    local_task_id: str
    external_document_reference: str | None
    base_sha256: str | None
    status: ImportStatus
    result_code: str | None
    k3_result: K3Result
    submitted_at: datetime | None
    created_at: datetime
    updated_at: datetime
    attachments: tuple[ImportAttachmentView, ...]


@dataclass(frozen=True)
class OwnerImportJobView:
    id: UUID
    project_id: UUID
    local_task_id: str
    external_document_reference: str | None
    model_label: str
    status: ImportStatus
    result_code: str | None
    submitted_at: datetime | None
    created_at: datetime
    updated_at: datetime
    attachments: tuple[ImportAttachmentView, ...]


class ImportService:
    """Create import jobs around the existing durable File/Upload lifecycle."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        storage: ObjectStorage,
        enqueue_scan: Callable[[UUID, UUID], Awaitable[None] | None] | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.storage = storage
        self.enqueue_scan = enqueue_scan

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
            audit_project_id = await self._resolved_project_id(command.project_id)
            await self._record_denial(actor, audit_project_id, request_id)
            raise ForbiddenError("IMPORT_CREATE_FORBIDDEN", "Import creation is not permitted")

        manifest_bytes = canonical_manifest_bytes(command)
        manifest_fingerprint = hashlib.sha256(manifest_bytes).hexdigest()
        canonical_manifest = command.model_dump(mode="json")
        async with self.session_factory() as existing_session:
            existing = await self._existing_result(
                existing_session,
                actor.subject_id,
                idempotency_key,
                manifest_fingerprint,
            )
            if existing is not None:
                return existing

        uploads: list[File] = []
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
            except ConflictError as error:
                if error.code != "FILE_UPLOAD_CONFLICT":
                    raise
                raise ConflictError(
                    "IMPORT_IDEMPOTENCY_CONFLICT",
                    "Idempotency key is already bound to another import manifest",
                ) from error
            uploads.append(upload)

        async with self.session_factory() as persist_session, persist_session.begin():
            return await self._persist_job(
                persist_session,
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
            return await FileService(
                file_session, self.storage, self.enqueue_scan
            ).complete_import_upload(
                actor,
                target.upload_id,
                parts,
                request_id,
            )

    async def view_job(self, job_id: UUID) -> ImportJobView:
        """Render a server-owned job ID already authorized by a mutating operation."""
        async with self.session_factory() as session:
            job = await session.get(ImportJob, job_id)
            if job is None:
                raise NotFoundError("IMPORT_JOB_NOT_FOUND", "Import job not found")
            return await self._job_view(session, job)

    async def view_attachment(
        self,
        job_id: UUID,
        attachment_id: UUID,
    ) -> ImportAttachmentView:
        """Render an attachment already authorized by the completion operation."""
        async with self.session_factory() as session:
            row = (
                await session.execute(
                    select(ImportAttachment, File.state)
                    .join(
                        File,
                        and_(
                            File.id == ImportAttachment.file_id,
                            File.project_id == ImportAttachment.project_id,
                        ),
                    )
                    .where(
                        ImportAttachment.job_id == job_id,
                        ImportAttachment.id == attachment_id,
                    )
                )
            ).one_or_none()
            if row is None:
                raise NotFoundError("IMPORT_ATTACHMENT_NOT_FOUND", "Import attachment not found")
            attachment, file_state = row
            return self._attachment_view(attachment, file_state)

    async def read(
        self,
        actor: Actor,
        job_id: UUID,
        *,
        request_id: UUID,
    ) -> ImportJobView:
        if (
            actor.kind != "device"
            or actor.role is not None
            or "imports:read-own" not in actor.scopes
        ):
            forbidden = ForbiddenError(
                "IMPORT_READ_FORBIDDEN",
                "Import read is not permitted",
            )
            await self._record_read_denial(
                actor,
                job_id,
                project_id=None,
                request_id=request_id,
                error=forbidden,
            )
            raise forbidden

        denial: DomainError | None
        project_id: UUID | None
        file_ids: list[UUID] = []
        async with self.session_factory() as session:
            job = await session.get(ImportJob, job_id)
            if job is None or job.device_id != actor.subject_id:
                denial = NotFoundError("IMPORT_JOB_NOT_FOUND", "Import job not found")
                project_id = None
            elif job.project_id not in actor.project_ids:
                denial = ForbiddenError(
                    "IMPORT_READ_FORBIDDEN",
                    "Import read is not permitted",
                )
                project_id = job.project_id
            else:
                denial = None
                project_id = job.project_id
                file_ids = list(
                    await session.scalars(
                        select(ImportAttachment.file_id)
                        .where(ImportAttachment.job_id == job.id)
                        .order_by(ImportAttachment.file_id)
                    )
                )

        if denial is not None:
            await self._record_read_denial(
                actor,
                job_id,
                project_id=project_id,
                request_id=request_id,
                error=denial,
            )
            raise denial

        for file_id in file_ids:
            await self.reconcile_file(file_id)

        async with self.session_factory() as session:
            job = await session.get(ImportJob, job_id)
            if (
                job is None
                or job.device_id != actor.subject_id
                or job.project_id != project_id
            ):
                denial = NotFoundError("IMPORT_JOB_NOT_FOUND", "Import job not found")
            elif job.project_id not in actor.project_ids:
                denial = ForbiddenError(
                    "IMPORT_READ_FORBIDDEN",
                    "Import read is not permitted",
                )
            else:
                return await self._job_view(session, job)

        await self._record_read_denial(
            actor,
            job_id,
            project_id=project_id,
            request_id=request_id,
            error=denial,
        )
        raise denial

    async def list_owner(
        self,
        actor: Actor,
        *,
        limit: int,
        request_id: UUID,
    ) -> list[OwnerImportJobView]:
        if actor.kind != "user" or actor.role != Role.OWNER:
            await AuditService(self.session_factory).record(
                AuditEventInput(
                    actor=actor,
                    action="import.list",
                    object_type="import_job",
                    outcome="DENIED",
                    request_id=request_id,
                    metadata={"reason": "OWNER_REQUIRED"},
                )
            )
            raise ForbiddenError("OWNER_REQUIRED", "Owner access required")
        if not 1 <= limit <= 100:
            raise ValueError("import list limit must be in 1..100")

        async with self.session_factory() as session:
            jobs = list(
                await session.scalars(
                    select(ImportJob)
                    .order_by(ImportJob.created_at.desc(), ImportJob.id.desc())
                    .limit(limit)
                )
            )
            views = await self._job_views(session, jobs)
        return [
            OwnerImportJobView(
                id=view.id,
                project_id=view.project_id,
                local_task_id=view.local_task_id,
                external_document_reference=view.external_document_reference,
                model_label=view.k3_result.model_label,
                status=view.status,
                result_code=view.result_code,
                submitted_at=view.submitted_at,
                created_at=view.created_at,
                updated_at=view.updated_at,
                attachments=view.attachments,
            )
            for view in views
        ]

    async def _record_read_denial(
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
                action="import.read",
                object_type="import_job",
                object_id=job_id,
                project_id=project_id,
                outcome="DENIED",
                request_id=request_id,
                metadata={"error_code": error.code, "job_id": str(job_id)},
            )
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
                            File.id == ImportAttachment.upload_id,
                        ),
                    )
                    .where(
                        ImportJob.id == job_id,
                        ImportAttachment.id == attachment_id,
                    )
                )
            ).one_or_none()

        if row is None or row.device_id != actor.subject_id:
            not_found_error = NotFoundError("IMPORT_ATTACHMENT_NOT_FOUND", "Import attachment not found")
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
                denial = NotFoundError("IMPORT_JOB_NOT_FOUND", "Import job not found")
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
                    denial = ConflictError(
                        "IMPORT_ATTACHMENTS_INCOMPLETE",
                        "Import attachments are incomplete",
                    )
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
            not_found = NotFoundError("IMPORT_JOB_NOT_FOUND", "Import job not found")
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
                    "import.reconcile",
                    job.id,
                    evaluation.status,
                    evaluation.result_code,
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

    async def _job_view(
        self,
        session: AsyncSession,
        job: ImportJob,
    ) -> ImportJobView:
        views = await self._job_views(session, [job])
        if not views:
            raise NotFoundError("IMPORT_JOB_NOT_FOUND", "Import job not found")
        return views[0]

    async def _job_views(
        self,
        session: AsyncSession,
        jobs: list[ImportJob],
    ) -> list[ImportJobView]:
        if not jobs:
            return []
        job_ids = [job.id for job in jobs]
        attachments = list(
            await session.scalars(
                select(ImportAttachment)
                .where(ImportAttachment.job_id.in_(job_ids))
                .order_by(
                    ImportAttachment.job_id,
                    ImportAttachment.kind,
                    ImportAttachment.id,
                )
            )
        )
        file_ids = [attachment.file_id for attachment in attachments]
        states_by_file: dict[UUID, FileState] = {}
        if file_ids:
            states_by_file = {
                file_id: file_state
                for file_id, file_state in (
                    await session.execute(
                        select(File.id, File.state).where(File.id.in_(file_ids))
                    )
                ).all()
            }
        attachments_by_job: dict[UUID, list[ImportAttachmentView]] = {
            job_id: [] for job_id in job_ids
        }
        for attachment in attachments:
            file_state = states_by_file.get(attachment.file_id)
            if file_state is None:
                raise RuntimeError("import attachment file is missing")
            attachments_by_job[attachment.job_id].append(
                self._attachment_view(attachment, file_state)
            )

        views: list[ImportJobView] = []
        for job in jobs:
            manifest = ImportJobCreate.model_validate(job.canonical_manifest_json)
            views.append(
                ImportJobView(
                    id=job.id,
                    project_id=job.project_id,
                    local_task_id=job.local_task_id,
                    external_document_reference=job.external_document_reference,
                    base_sha256=job.base_sha256,
                    status=job.status,
                    result_code=job.result_code,
                    k3_result=manifest.k3_result,
                    submitted_at=job.submitted_at,
                    created_at=job.created_at,
                    updated_at=job.updated_at,
                    attachments=tuple(attachments_by_job[job.id]),
                )
            )
        return views

    @staticmethod
    def _attachment_view(
        attachment: ImportAttachment,
        file_state: FileState,
    ) -> ImportAttachmentView:
        return ImportAttachmentView(
            id=attachment.id,
            file_id=attachment.file_id,
            upload_id=attachment.upload_id,
            kind=attachment.kind,
            file_state=file_state,
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
        event_key = cls._transition_event_key(
            action, job.id, evaluation.status, evaluation.result_code
        )
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
    def _transition_event_key(
        action: str,
        job_id: UUID,
        status: ImportStatus,
        result_code: str | None = None,
    ) -> UUID:
        return uuid5(
            NAMESPACE_URL,
            f"superboss:{action}:{job_id}:{status.value}:{result_code or ''}",
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
            updated_at=job.updated_at,
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
        project_id: UUID | None,
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

    async def _resolved_project_id(self, project_id: UUID) -> UUID | None:
        async with self.session_factory() as session:
            project = await session.get(Project, project_id)
            return None if project is None else project.id

    async def _existing_result(
        self,
        session: AsyncSession,
        device_id: UUID,
        idempotency_key: str,
        manifest_fingerprint: str,
    ) -> ImportJobResult | None:
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
        session: AsyncSession,
        actor: Actor,
        command: ImportJobCreate,
        idempotency_key: str,
        canonical_manifest: dict[str, object],
        manifest_fingerprint: str,
        uploads: list[File],
        request_id: UUID,
    ) -> ImportJobResult:
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
        try:
            async with session.begin_nested():
                session.add(job)
                await session.flush()
        except IntegrityError as error:
            is_idempotency = self._constraint_name(error) == (
                "uq_import_jobs_device_idempotency"
            )
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
                file_id=upload.id,
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
        await session.flush()
        return ImportJobResult(
            id=job.id,
            status=job.status,
            result_code=job.result_code,
            submitted_at=job.submitted_at,
            updated_at=job.updated_at,
            attachments=tuple(self._attachment_result(row) for row in attachment_rows),
        )

    async def _matching_result(
        self,
        session: AsyncSession,
        job: ImportJob,
        manifest_fingerprint: str,
    ) -> ImportJobResult:
        if job.manifest_fingerprint != manifest_fingerprint:
            raise ConflictError(
                "IMPORT_IDEMPOTENCY_CONFLICT",
                "Idempotency key is already bound to another import manifest",
            )
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
            updated_at=job.updated_at,
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
