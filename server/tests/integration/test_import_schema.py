"""PostgreSQL contracts for normalized K3 import persistence."""

import asyncio
import importlib
import json
import os
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import ModuleType
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import asyncpg
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
IMPORT_TABLES = {"import_jobs", "import_attachments"}


def import_models() -> ModuleType:
    """Load the wished-for models lazily so RED identifies the missing module."""
    try:
        return importlib.import_module("superboss.modules.imports.models")
    except ModuleNotFoundError as error:
        pytest.fail(f"Task 10 imports models are not implemented ({error.name})")


def _database_url(database_url: str, database_name: str) -> str:
    parsed = urlsplit(database_url)
    return urlunsplit((parsed.scheme, parsed.netloc, f"/{database_name}", "", ""))


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
    assert IMPORT_TABLES <= set(Base.metadata.tables)
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


def test_0017_round_trip_and_populated_downgrade_guard_are_atomic(
    postgres_database: str,
) -> None:
    """0017 must round-trip empty and refuse destructive DDL before losing any import state."""
    temporary_name = f"superboss_imports_{uuid4().hex}"
    admin_url = _database_url(postgres_database, "postgres")
    temporary_url = _database_url(postgres_database, temporary_name)
    pg_url = temporary_url.replace("postgresql+asyncpg", "postgresql")

    async def create_database() -> None:
        connection = await asyncpg.connect(
            admin_url.replace("postgresql+asyncpg", "postgresql")
        )
        try:
            await connection.execute(f'CREATE DATABASE "{temporary_name}"')
        finally:
            await connection.close()

    async def drop_database() -> None:
        connection = await asyncpg.connect(
            admin_url.replace("postgresql+asyncpg", "postgresql")
        )
        try:
            await connection.execute(
                f'DROP DATABASE IF EXISTS "{temporary_name}" WITH (FORCE)'
            )
        finally:
            await connection.close()

    async def snapshot() -> tuple[
        str,
        set[str],
        tuple[tuple[object, ...], ...],
        tuple[tuple[object, ...], ...],
        tuple[tuple[object, ...], ...],
        tuple[tuple[object, ...], ...],
        tuple[tuple[object, ...], ...],
    ]:
        connection = await asyncpg.connect(pg_url)
        try:
            revision = str(await connection.fetchval("SELECT version_num FROM alembic_version"))
            tables = {
                row["tablename"]
                for row in await connection.fetch(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
                )
            }
            columns = await connection.fetch(
                "SELECT table_name, column_name, data_type, is_nullable, ordinal_position "
                "FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = ANY($1::text[]) "
                "ORDER BY table_name, ordinal_position",
                sorted(IMPORT_TABLES),
            )
            constraints = await connection.fetch(
                "SELECT relation.relname, constraint_.conname, constraint_.contype, "
                "pg_get_constraintdef(constraint_.oid) AS definition "
                "FROM pg_constraint AS constraint_ "
                "JOIN pg_class AS relation ON relation.oid = constraint_.conrelid "
                "JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
                "WHERE namespace.nspname = 'public' AND relation.relname = ANY($1::text[]) "
                "ORDER BY relation.relname, constraint_.conname",
                sorted(IMPORT_TABLES | {"uploads"}),
            )
            indexes = await connection.fetch(
                "SELECT tablename, indexname, indexdef FROM pg_indexes "
                "WHERE schemaname = 'public' AND tablename = ANY($1::text[]) "
                "ORDER BY tablename, indexname",
                sorted(IMPORT_TABLES | {"uploads"}),
            )
            jobs = ()
            attachments = ()
            if IMPORT_TABLES <= tables:
                jobs = tuple(
                    tuple(row)
                    for row in await connection.fetch(
                        "SELECT id::text, device_id::text, project_id::text, idempotency_key, "
                        "local_task_id, external_document_reference, base_sha256, "
                        "canonical_manifest_json::text, manifest_fingerprint, status, "
                        "result_code, submitted_at, created_at, updated_at "
                        "FROM import_jobs ORDER BY id"
                    )
                )
                attachments = tuple(
                    tuple(row)
                    for row in await connection.fetch(
                        "SELECT id::text, job_id::text, project_id::text, file_id::text, "
                        "upload_id::text, kind, created_at "
                        "FROM import_attachments ORDER BY id"
                    )
                )
            return (
                revision,
                tables,
                tuple(tuple(row) for row in columns),
                tuple(tuple(row) for row in constraints),
                tuple(tuple(row) for row in indexes),
                jobs,
                attachments,
            )
        finally:
            await connection.close()

    async def column_limit(table_name: str, column_name: str) -> int | None:
        connection = await asyncpg.connect(pg_url)
        try:
            value = await connection.fetchval(
                "SELECT character_maximum_length FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = $1 AND column_name = $2",
                table_name,
                column_name,
            )
            return int(value) if value is not None else None
        finally:
            await connection.close()

    async def seed_import() -> None:
        connection = await asyncpg.connect(pg_url)
        owner_id, project_id, device_id = uuid4(), uuid4(), uuid4()
        file_id, upload_id, job_id, attachment_id = uuid4(), uuid4(), uuid4(), uuid4()
        try:
            await connection.execute(
                "INSERT INTO users (id, wecom_userid, display_name, role, status) "
                "VALUES ($1, $2, 'Owner', 'OWNER', 'ACTIVE')",
                owner_id,
                f"owner-{owner_id}",
            )
            await connection.execute(
                "INSERT INTO projects (id, name, is_test, status) "
                "VALUES ($1, $2, FALSE, 'ACTIVE')",
                project_id,
                f"Import project {project_id}",
            )
            await connection.execute(
                "INSERT INTO device_connections (id, owner_id, name) VALUES ($1, $2, $3)",
                device_id,
                owner_id,
                f"Import device {device_id}",
            )
            await connection.execute(
                "INSERT INTO files (id, project_id, filename, category, file_date, object_key, "
                "size_bytes, sha256, state, uploader_kind, uploader_id, content_type) "
                "VALUES ($1, $2, 'k3.json', 'kimi-imports', '2026-08-09', $3, 1, $4, "
                "'UPLOADING', 'device', $5, 'application/json')",
                file_id,
                project_id,
                f"projects/{project_id}/kimi-imports/{file_id}/k3.json",
                "b" * 64,
                device_id,
            )
            await connection.execute(
                "INSERT INTO uploads (id, file_id, project_id, uploader_kind, uploader_id, "
                "idempotency_key, metadata_fingerprint, multipart_id) "
                "VALUES ($1, $2, $3, 'device', $4, 'import-child', $5, 'multipart-import')",
                upload_id,
                file_id,
                project_id,
                device_id,
                "c" * 64,
            )
            await connection.execute(
                "INSERT INTO import_jobs "
                "(id, device_id, project_id, idempotency_key, local_task_id, "
                "external_document_reference, base_sha256, canonical_manifest_json, "
                "manifest_fingerprint, status, result_code, submitted_at) "
                "VALUES ($1, $2, $3, 'import-key', 'local-task', NULL, NULL, $4::jsonb, "
                "$5, 'UPLOADING', NULL, NULL)",
                job_id,
                device_id,
                project_id,
                '{"k3_result":{"model_label":"K3"}}',
                "d" * 64,
            )
            await connection.execute(
                "INSERT INTO import_attachments "
                "(id, job_id, project_id, file_id, upload_id, kind) "
                "VALUES ($1, $2, $3, $4, $5, 'K3_RAW')",
                attachment_id,
                job_id,
                project_id,
                file_id,
                upload_id,
            )
        finally:
            await connection.close()

    environment = os.environ.copy()
    environment["SUPERBOSS_DATABASE_URL"] = temporary_url
    asyncio.run(create_database())
    try:
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "0016_device_connections"],
            cwd=SERVER_ROOT,
            env=environment,
            check=True,
        )
        baseline = asyncio.run(snapshot())
        assert baseline[0] == "0016_device_connections"
        assert not IMPORT_TABLES & baseline[1]

        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=SERVER_ROOT,
            env=environment,
            check=True,
        )
        head = asyncio.run(snapshot())
        assert head[0] == "0017_import_jobs"
        assert IMPORT_TABLES <= head[1]
        assert {row[0] for row in head[2]} == IMPORT_TABLES
        definitions = {
            (row[0], row[1]): row[3]
            for row in head[3]
        }
        assert {
            "ck_import_jobs_manifest_fingerprint",
            "ck_import_jobs_manifest_size",
            "ck_import_jobs_result_code",
            "ck_import_jobs_result_state",
            "ck_import_jobs_status",
            "ck_import_jobs_submission_state",
            "ck_import_jobs_time_order",
            "uq_import_jobs_device_idempotency",
            "uq_import_jobs_id_project",
        } <= {name for table, name in definitions if table == "import_jobs"}
        assert {
            "ck_import_attachments_kind",
            "fk_import_attachments_file_project",
            "fk_import_attachments_job_project",
            "fk_import_attachments_upload_file_project",
            "uq_import_attachments_file",
            "uq_import_attachments_job_kind",
            "uq_import_attachments_upload",
        } <= {name for table, name in definitions if table == "import_attachments"}
        assert "uq_uploads_id_file_project" in {
            name for table, name in definitions if table == "uploads"
        }
        normalized_job_unique = definitions[
            ("import_jobs", "uq_import_jobs_device_idempotency")
        ].replace(" ", "")
        assert "UNIQUE(device_id,idempotency_key)" in normalized_job_unique
        status_definition = definitions[("import_jobs", "ck_import_jobs_status")]
        assert all(
            status in status_definition
            for status in ("UPLOADING", "SCANNING", "RECEIVED", "REJECTED", "CONFLICT")
        )
        assert "K3_RAW" in definitions[
            ("import_attachments", "ck_import_attachments_kind")
        ]
        assert asyncio.run(column_limit("import_jobs", "result_code")) == 64

        subprocess.run(
            [sys.executable, "-m", "alembic", "downgrade", "0016_device_connections"],
            cwd=SERVER_ROOT,
            env=environment,
            check=True,
        )
        assert asyncio.run(snapshot()) == baseline
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=SERVER_ROOT,
            env=environment,
            check=True,
        )

        asyncio.run(seed_import())
        before = asyncio.run(snapshot())
        downgrade = subprocess.run(
            [sys.executable, "-m", "alembic", "downgrade", "0016_device_connections"],
            cwd=SERVER_ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        assert downgrade.returncode != 0
        assert "SUPERBOSS_IMPORT_DOWNGRADE_BLOCKED" in downgrade.stdout + downgrade.stderr
        assert asyncio.run(snapshot()) == before
    finally:
        asyncio.run(drop_database())
