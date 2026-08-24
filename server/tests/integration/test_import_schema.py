"""PostgreSQL contracts for normalized K3 import persistence."""

import importlib
import json
import os
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import ModuleType
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from superboss.core.db import Base
from superboss.modules.devices.models import DeviceConnection
from superboss.modules.files.models import File, FileState, Upload
from superboss.modules.projects.models import Project
from superboss.modules.users.models import User

SERVER_ROOT = Path(__file__).resolve().parents[2]


def import_models() -> ModuleType:
    """Load the wished-for models lazily so RED identifies the missing module."""
    try:
        return importlib.import_module("superboss.modules.imports.models")
    except ModuleNotFoundError as error:
        pytest.fail(f"Task 10 imports models are not implemented ({error.name})")


def _constraint_name(error: DBAPIError) -> str | None:
    cause = getattr(error.orig, "__cause__", None)
    return getattr(cause, "constraint_name", None)


async def _seed_import_parent(
    db_session: AsyncSession,
    active_owner: User,
    *,
    name: str,
) -> tuple[Project, DeviceConnection]:
    project = Project(name=name)
    device = DeviceConnection(owner_id=active_owner.id, name=f"{name} device")
    db_session.add_all([project, device])
    await db_session.commit()
    return project, device


_IMPORT_JOB_INSERT = text(
    "INSERT INTO import_jobs "
    "(id, device_id, project_id, idempotency_key, local_task_id, "
    "external_document_reference, base_sha256, canonical_manifest_json, "
    "manifest_fingerprint, status, result_code, submitted_at, created_at, updated_at) "
    "VALUES (:id, :device_id, :project_id, :idempotency_key, :local_task_id, "
    "NULL, NULL, CAST(:manifest AS jsonb), :fingerprint, :status, :result_code, "
    ":submitted_at, :created_at, :updated_at)"
)


def _job_values(
    project: Project,
    device: DeviceConnection,
    *,
    status: str = "UPLOADING",
    result_code: str | None = None,
    submitted_at: datetime | None = None,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
    manifest: str = "{}",
) -> dict[str, object]:
    now = created_at or datetime(2026, 8, 10, 1, 0, tzinfo=UTC)
    return {
        "id": uuid4(),
        "device_id": device.id,
        "project_id": project.id,
        "idempotency_key": f"direct-{uuid4().hex}",
        "local_task_id": f"local-{uuid4().hex}",
        "manifest": manifest,
        "fingerprint": uuid4().hex + uuid4().hex,
        "status": status,
        "result_code": result_code,
        "submitted_at": submitted_at,
        "created_at": now,
        "updated_at": updated_at or now,
    }


def test_import_models_are_registered_and_alembic_has_no_metadata_drift(
    postgres_database: str,
) -> None:
    """Forgetting migrations/env.py registration would make alembic check propose destructive drift."""
    import_models()
    assert {"import_jobs", "import_attachments"} <= set(Base.metadata.tables)
    assert "import_idempotency_claims" not in Base.metadata.tables
    environment = os.environ.copy()
    environment["SUPERBOSS_DATABASE_URL"] = postgres_database

    check = subprocess.run(
        [sys.executable, "-m", "alembic", "check"],
        cwd=SERVER_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert check.returncode == 0, check.stdout + check.stderr
    assert "No new upgrade operations detected" in check.stdout + check.stderr


@pytest.mark.asyncio
async def test_database_rejects_import_attachment_from_a_different_file_project(
    db_session: AsyncSession,
    active_owner: User,
) -> None:
    """Direct database writers must not splice a foreign-project File into a job."""
    models = import_models()
    project_a = Project(name="Import project A")
    project_b = Project(name="Import project B")
    device = DeviceConnection(owner_id=active_owner.id, name="Import schema device")
    db_session.add_all([project_a, project_b, device])
    await db_session.flush()
    foreign_file = File(
        project_id=project_b.id,
        filename="foreign.json",
        category="kimi-imports",
        file_date=date(2026, 8, 9),
        object_key=f"projects/{project_b.id}/kimi-imports/foreign.json",
        size_bytes=1,
        sha256="b" * 64,
        state=FileState.UPLOADING,
        uploader_id=device.id,
        uploader_kind="device",
        content_type="application/json",
    )
    db_session.add(foreign_file)
    await db_session.flush()
    foreign_upload = Upload(
        file_id=foreign_file.id,
        project_id=project_b.id,
        uploader_id=device.id,
        uploader_kind="device",
        idempotency_key="import-schema-child",
        metadata_fingerprint="c" * 64,
        multipart_id="multipart-import-schema",
    )
    job = models.ImportJob(
        device_id=device.id,
        project_id=project_a.id,
        idempotency_key="import-schema-job",
        local_task_id="local-schema-job",
        external_document_reference=None,
        base_sha256=None,
        canonical_manifest_json={"schema": "bounded"},
        manifest_fingerprint="d" * 64,
        status=models.ImportStatus.UPLOADING,
        result_code=None,
        submitted_at=None,
    )
    db_session.add_all([foreign_upload, job])
    await db_session.flush()
    db_session.add(
        models.ImportAttachment(
            job_id=job.id,
            project_id=project_a.id,
            file_id=foreign_file.id,
            upload_id=foreign_upload.id,
            kind=models.AttachmentKind.K3_RAW,
        )
    )

    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_database_rejects_same_project_file_and_upload_pair_splicing(
    db_session: AsyncSession,
    active_owner: User,
) -> None:
    """A valid Upload B may not be paired with File A merely because projects match."""
    models = import_models()
    project, device = await _seed_import_parent(
        db_session, active_owner, name="Same-project attachment pairing"
    )
    files = [
        File(
            project_id=project.id,
            filename=f"file-{label}.json",
            category="kimi-imports",
            file_date=date(2026, 8, 10),
            object_key=f"projects/{project.id}/kimi-imports/{label}-{uuid4()}.json",
            size_bytes=1,
            sha256=digest * 64,
            state=FileState.UPLOADING,
            uploader_id=device.id,
            uploader_kind="device",
            content_type="application/json",
        )
        for label, digest in (("a", "a"), ("b", "b"))
    ]
    db_session.add_all(files)
    await db_session.flush()
    uploads = [
        Upload(
            file_id=file.id,
            project_id=project.id,
            uploader_id=device.id,
            uploader_kind="device",
            idempotency_key=f"pair-{index}",
            metadata_fingerprint=str(index) * 64,
            multipart_id=f"multipart-{index}",
        )
        for index, file in enumerate(files, start=1)
    ]
    job = models.ImportJob(
        device_id=device.id,
        project_id=project.id,
        idempotency_key="pairing-job",
        local_task_id="pairing-local",
        external_document_reference=None,
        base_sha256=None,
        canonical_manifest_json={"schema": "bounded"},
        manifest_fingerprint="d" * 64,
        status=models.ImportStatus.UPLOADING,
        result_code=None,
        submitted_at=None,
    )
    db_session.add_all([*uploads, job])
    await db_session.commit()

    with pytest.raises(IntegrityError) as mismatch:
        async with db_session.begin_nested():
            db_session.add(
                models.ImportAttachment(
                    job_id=job.id,
                    project_id=project.id,
                    file_id=files[0].id,
                    upload_id=uploads[1].id,
                    kind=models.AttachmentKind.K3_RAW,
                )
            )
            await db_session.flush()

    assert _constraint_name(mismatch.value) == (
        "fk_import_attachments_upload_file_project"
    )
    db_session.add(
        models.ImportAttachment(
            job_id=job.id,
            project_id=project.id,
            file_id=files[0].id,
            upload_id=uploads[0].id,
            kind=models.AttachmentKind.K3_RAW,
        )
    )
    await db_session.commit()
    assert await db_session.scalar(
        select(func.count()).select_from(models.ImportAttachment)
    ) == 1


@pytest.mark.asyncio
async def test_database_rejects_oversized_manifest_json_on_insert_and_update(
    db_session: AsyncSession,
    active_owner: User,
) -> None:
    """JSONB writes outside the API must use the same 65,536 UTF-8-octet ceiling."""
    models = import_models()
    project, device = await _seed_import_parent(
        db_session, active_owner, name="Manifest octet constraint"
    )
    oversized = json.dumps(
        {"payload": "界" * 30_000},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    assert len(oversized.encode("utf-8")) > 65_536
    oversized_values = _job_values(
        project,
        device,
        manifest=oversized,
    )

    with pytest.raises(IntegrityError) as insert_error:
        async with db_session.begin_nested():
            await db_session.execute(_IMPORT_JOB_INSERT, oversized_values)

    assert _constraint_name(insert_error.value) == "ck_import_jobs_manifest_size"
    valid_values = _job_values(project, device, manifest='{"payload":"ok"}')
    await db_session.execute(_IMPORT_JOB_INSERT, valid_values)
    await db_session.commit()

    with pytest.raises(IntegrityError) as update_error:
        async with db_session.begin_nested():
            await db_session.execute(
                text(
                    "UPDATE import_jobs SET canonical_manifest_json = CAST(:manifest AS jsonb) "
                    "WHERE id = :job_id"
                ),
                {"manifest": oversized, "job_id": valid_values["id"]},
            )

    assert _constraint_name(update_error.value) == "ck_import_jobs_manifest_size"
    persisted = await db_session.scalar(
        text("SELECT canonical_manifest_json::text FROM import_jobs WHERE id = :job_id"),
        {"job_id": valid_values["id"]},
    )
    assert persisted == '{"payload": "ok"}'
    assert await db_session.scalar(
        select(func.count()).select_from(models.ImportJob)
    ) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "expected_constraint"),
    [
        ("uploading_with_submitted", "ck_import_jobs_submission_state"),
        ("scanning_without_submitted", "ck_import_jobs_submission_state"),
        ("received_without_submitted", "ck_import_jobs_submission_state"),
        ("rejected_without_submitted", "ck_import_jobs_submission_state"),
        ("conflict_without_submitted", "ck_import_jobs_submission_state"),
        ("received_with_result", "ck_import_jobs_result_state"),
        ("rejected_without_result", "ck_import_jobs_result_state"),
        ("conflict_without_result", "ck_import_jobs_result_state"),
        ("rejected_blank_result", "ck_import_jobs_result_code"),
        ("conflict_control_result", "ck_import_jobs_result_code"),
        ("rejected_overlong_result", None),
        ("submitted_before_created", "ck_import_jobs_time_order"),
        ("submitted_after_updated", "ck_import_jobs_time_order"),
        ("updated_before_created", "ck_import_jobs_time_order"),
    ],
)
async def test_database_rejects_illegal_import_status_result_and_time_combinations(
    db_session: AsyncSession,
    active_owner: User,
    case: str,
    expected_constraint: str | None,
) -> None:
    """Direct writers may not create impossible state/result/timestamp combinations."""
    import_models()
    project, device = await _seed_import_parent(
        db_session, active_owner, name=f"Illegal import state {case}"
    )
    now = datetime(2026, 8, 10, 2, 0, tzinfo=UTC)
    values = _job_values(project, device, created_at=now, updated_at=now)
    if case == "uploading_with_submitted":
        values["submitted_at"] = now
    elif case.endswith("_without_submitted"):
        values["status"] = case.removesuffix("_without_submitted").upper()
        if values["status"] in {"REJECTED", "CONFLICT"}:
            values["result_code"] = "SAFE_RESULT"
    elif case == "received_with_result":
        values.update(status="RECEIVED", submitted_at=now, result_code="UNEXPECTED")
    elif case == "rejected_without_result":
        values.update(status="REJECTED", submitted_at=now, result_code=None)
    elif case == "conflict_without_result":
        values.update(status="CONFLICT", submitted_at=now, result_code=None)
    elif case == "rejected_blank_result":
        values.update(status="REJECTED", submitted_at=now, result_code="")
    elif case == "conflict_control_result":
        values.update(status="CONFLICT", submitted_at=now, result_code="BAD\nCODE")
    elif case == "rejected_overlong_result":
        values.update(status="REJECTED", submitted_at=now, result_code="R" * 65)
    elif case == "submitted_before_created":
        values.update(status="SCANNING", submitted_at=now - timedelta(seconds=1))
    elif case == "submitted_after_updated":
        values.update(status="SCANNING", submitted_at=now + timedelta(seconds=1))
    else:
        values["updated_at"] = now - timedelta(seconds=1)

    with pytest.raises(DBAPIError) as rejected:
        async with db_session.begin_nested():
            await db_session.execute(_IMPORT_JOB_INSERT, values)

    if expected_constraint is not None:
        assert _constraint_name(rejected.value) == expected_constraint


@pytest.mark.asyncio
@pytest.mark.parametrize("write_kind", ["insert", "update"])
@pytest.mark.parametrize(
    "invalid_result_code",
    [" ", "BAD-CODE", "lowercase", "_LEADING"],
)
async def test_database_rejects_non_token_result_codes_on_insert_and_update(
    db_session: AsyncSession,
    active_owner: User,
    write_kind: str,
    invalid_result_code: str,
) -> None:
    """Terminal result codes are stable server tokens, never free-form text."""
    project, device = await _seed_import_parent(
        db_session,
        active_owner,
        name=f"Invalid result token {write_kind} {uuid4()}",
    )
    now = datetime(2026, 8, 10, 2, 30, tzinfo=UTC)
    values = _job_values(
        project,
        device,
        status="REJECTED",
        result_code=(
            invalid_result_code if write_kind == "insert" else "ATTACHMENT_INFECTED"
        ),
        submitted_at=now,
        created_at=now,
        updated_at=now,
    )
    if write_kind == "update":
        await db_session.execute(_IMPORT_JOB_INSERT, values)
        await db_session.commit()

    with pytest.raises(DBAPIError) as rejected:
        async with db_session.begin_nested():
            if write_kind == "insert":
                await db_session.execute(_IMPORT_JOB_INSERT, values)
            else:
                await db_session.execute(
                    text(
                        "UPDATE import_jobs SET result_code = :result_code "
                        "WHERE id = :job_id"
                    ),
                    {
                        "result_code": invalid_result_code,
                        "job_id": values["id"],
                    },
                )

    assert _constraint_name(rejected.value) == "ck_import_jobs_result_code"
    if write_kind == "update":
        persisted = await db_session.scalar(
            text("SELECT result_code FROM import_jobs WHERE id = :job_id"),
            {"job_id": values["id"]},
        )
        assert persisted == "ATTACHMENT_INFECTED"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "result_code"),
    [
        ("REJECTED", "ATTACHMENT_INFECTED"),
        ("REJECTED", "ATTACHMENT_SCAN_FAILED"),
        ("CONFLICT", "BASE_SHA256_MISMATCH"),
    ],
)
async def test_database_accepts_every_current_server_result_code(
    db_session: AsyncSession,
    active_owner: User,
    status: str,
    result_code: str,
) -> None:
    """The DB grammar must include every literal emitted by the import state machine."""
    models = import_models()
    project, device = await _seed_import_parent(
        db_session,
        active_owner,
        name=f"Legal fixed result {result_code}",
    )
    now = datetime(2026, 8, 10, 2, 45, tzinfo=UTC)

    await db_session.execute(
        _IMPORT_JOB_INSERT,
        _job_values(
            project,
            device,
            status=status,
            result_code=result_code,
            submitted_at=now,
            created_at=now,
            updated_at=now,
        ),
    )
    await db_session.commit()

    saved = await db_session.scalar(select(models.ImportJob))
    assert saved is not None
    assert saved.status.value == status
    assert saved.result_code == result_code


@pytest.mark.asyncio
async def test_database_accepts_each_legal_import_state_shape(
    db_session: AsyncSession,
    active_owner: User,
) -> None:
    """The state constraints must reject impossible rows without blocking all five states."""
    models = import_models()
    project, device = await _seed_import_parent(
        db_session, active_owner, name="Legal import states"
    )
    now = datetime(2026, 8, 10, 3, 0, tzinfo=UTC)
    legal = (
        ("UPLOADING", None, None),
        ("SCANNING", None, now),
        ("RECEIVED", None, now),
        ("REJECTED", "ATTACHMENT_FAILED", now),
        ("CONFLICT", "BASE_SHA256_MISMATCH", now),
    )
    for status, result_code, submitted_at in legal:
        await db_session.execute(
            _IMPORT_JOB_INSERT,
            _job_values(
                project,
                device,
                status=status,
                result_code=result_code,
                submitted_at=submitted_at,
                created_at=now,
                updated_at=now,
            ),
        )
    await db_session.commit()

    assert await db_session.scalar(
        select(func.count()).select_from(models.ImportJob)
    ) == len(legal)
