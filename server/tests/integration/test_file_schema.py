"""Pristine PostgreSQL migration coverage for resumable file persistence."""

import asyncio
import os
import subprocess
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from superboss.modules.files.models import File, FileState, Upload
from superboss.modules.projects.models import Project
from superboss.modules.users.models import User

SERVER_ROOT = Path(__file__).resolve().parents[2]
_DEVICE_TABLES = {
    "device_pairing_codes",
    "device_pairing_projects",
    "device_connections",
    "device_sessions",
    "device_project_grants",
    "device_scope_grants",
}
_IMPORT_TABLES = {
    "import_idempotency_claims",
    "import_jobs",
    "import_attachments",
}
_CatalogFingerprint = tuple[
    tuple[tuple[str, str, str, str, int], ...],
    tuple[tuple[str, str, str, str], ...],
    tuple[tuple[str, str, str], ...],
]


def _database_url(database_url: str, database_name: str) -> str:
    parsed = urlsplit(database_url)
    return urlunsplit((parsed.scheme, parsed.netloc, f"/{database_name}", "", ""))


async def _table_catalog(
    connection: asyncpg.Connection,
    table_names: set[str],
) -> _CatalogFingerprint:
    columns = await connection.fetch(
        "SELECT table_name, column_name, data_type, is_nullable, ordinal_position "
        "FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = ANY($1::text[]) "
        "ORDER BY table_name, ordinal_position",
        sorted(table_names),
    )
    constraints = await connection.fetch(
        "SELECT relation.relname, constraint_.conname, constraint_.contype, "
        "pg_get_constraintdef(constraint_.oid) AS definition "
        "FROM pg_constraint AS constraint_ "
        "JOIN pg_class AS relation ON relation.oid = constraint_.conrelid "
        "JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
        "WHERE namespace.nspname = 'public' AND relation.relname = ANY($1::text[]) "
        "ORDER BY relation.relname, constraint_.conname",
        sorted(table_names),
    )
    indexes = await connection.fetch(
        "SELECT tablename, indexname, indexdef FROM pg_indexes "
        "WHERE schemaname = 'public' AND tablename = ANY($1::text[]) "
        "ORDER BY tablename, indexname",
        sorted(table_names),
    )
    return (
        tuple(
            (
                row["table_name"],
                row["column_name"],
                row["data_type"],
                row["is_nullable"],
                row["ordinal_position"],
            )
            for row in columns
        ),
        tuple(
            (row["relname"], row["conname"], row["contype"], row["definition"])
            for row in constraints
        ),
        tuple(
            (row["tablename"], row["indexname"], row["indexdef"])
            for row in indexes
        ),
    )


async def _device_catalog(
    connection: asyncpg.Connection,
) -> _CatalogFingerprint:
    return await _table_catalog(connection, _DEVICE_TABLES)


async def _import_catalog(
    connection: asyncpg.Connection,
) -> _CatalogFingerprint:
    return await _table_catalog(connection, _IMPORT_TABLES)


def test_pristine_file_migration_round_trip_restores_0003_catalog(postgres_database: str) -> None:
    """Dropping 0004 must restore every pre-file table, constraint, and index exactly."""
    temporary_name = f"superboss_files_{uuid4().hex}"
    admin_url = _database_url(postgres_database, "postgres")
    temporary_url = _database_url(postgres_database, temporary_name)

    async def create_database() -> None:
        connection = await asyncpg.connect(admin_url.replace("postgresql+asyncpg", "postgresql"))
        try:
            await connection.execute(f'CREATE DATABASE "{temporary_name}"')
        finally:
            await connection.close()

    async def drop_database() -> None:
        connection = await asyncpg.connect(admin_url.replace("postgresql+asyncpg", "postgresql"))
        try:
            await connection.execute(f'DROP DATABASE IF EXISTS "{temporary_name}" WITH (FORCE)')
        finally:
            await connection.close()

    async def catalog() -> tuple[
        list[str], list[tuple[str, str, str, str]], list[tuple[str, str]]
    ]:
        connection = await asyncpg.connect(temporary_url.replace("postgresql+asyncpg", "postgresql"))
        try:
            tables = await connection.fetch(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
            )
            constraints = await connection.fetch(
                "SELECT relation.relname, constraint_.conname, constraint_.contype, "
                "pg_get_constraintdef(constraint_.oid) AS definition "
                "FROM pg_constraint AS constraint_ "
                "JOIN pg_class AS relation ON relation.oid = constraint_.conrelid "
                "JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
                "WHERE namespace.nspname = 'public' "
                "ORDER BY relation.relname, constraint_.conname"
            )
            indexes = await connection.fetch(
                "SELECT tablename, indexdef FROM pg_indexes "
                "WHERE schemaname = 'public' ORDER BY tablename, indexname"
            )
            return (
                [row["tablename"] for row in tables],
                [
                    (row["relname"], row["conname"], row["contype"], row["definition"])
                    for row in constraints
                ],
                [(row["tablename"], row["indexdef"]) for row in indexes],
            )
        finally:
            await connection.close()

    async def table_columns(table_name: str) -> list[str]:
        connection = await asyncpg.connect(temporary_url.replace("postgresql+asyncpg", "postgresql"))
        try:
            rows = await connection.fetch(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = $1 "
                "ORDER BY ordinal_position",
                table_name,
            )
            return [row["column_name"] for row in rows]
        finally:
            await connection.close()

    async def revision() -> str:
        connection = await asyncpg.connect(temporary_url.replace("postgresql+asyncpg", "postgresql"))
        try:
            return str(await connection.fetchval("SELECT version_num FROM alembic_version"))
        finally:
            await connection.close()

    async def trigger_names() -> set[str]:
        connection = await asyncpg.connect(temporary_url.replace("postgresql+asyncpg", "postgresql"))
        try:
            rows = await connection.fetch(
                "SELECT tgname FROM pg_trigger JOIN pg_class ON pg_class.oid = tgrelid "
                "WHERE relname = 'files' AND NOT tgisinternal"
            )
            return {row["tgname"] for row in rows}
        finally:
            await connection.close()

    legacy_project_id = uuid4()
    legacy_file_id = uuid4()
    legacy_upload_id = uuid4()

    async def insert_legacy_upload() -> None:
        connection = await asyncpg.connect(temporary_url.replace("postgresql+asyncpg", "postgresql"))
        try:
            await connection.execute(
                "INSERT INTO projects (id, name, is_test, status) VALUES ($1, $2, FALSE, 'ACTIVE')",
                legacy_project_id,
                f"Lifecycle {legacy_project_id.hex}",
            )
            await connection.execute(
                "INSERT INTO files (id, project_id, filename, category, file_date, object_key, "
                "size_bytes, sha256, state, uploader_kind, uploader_id, content_type) "
                "VALUES ($1, $2, 'legacy.pdf', 'docs', '2026-08-09', $3, 1, $4, 'UPLOADING', "
                "'user', $5, 'application/pdf')",
                legacy_file_id,
                legacy_project_id,
                f"projects/{legacy_project_id}/docs/2026-08-09/{legacy_file_id}/legacy.pdf",
                "0" * 64,
                uuid4(),
            )
            await connection.execute(
                "INSERT INTO uploads (id, file_id, project_id, uploader_kind, uploader_id, "
                "idempotency_key, metadata_fingerprint, multipart_id) "
                "VALUES ($1, $2, $3, 'user', $4, 'legacy-key', $5, 'legacy-multipart')",
                legacy_upload_id,
                legacy_file_id,
                legacy_project_id,
                uuid4(),
                "0" * 64,
            )
        finally:
            await connection.close()

    async def lifecycle_backfill() -> tuple[str, str, str, str, int]:
        connection = await asyncpg.connect(temporary_url.replace("postgresql+asyncpg", "postgresql"))
        try:
            row = await connection.fetchrow(
                "SELECT provision_state, completion_state, object_key, multipart_id, declared_size_bytes "
                "FROM file_upload_lifecycle WHERE upload_id = $1",
                legacy_upload_id,
            )
            assert row is not None
            return (
                row["provision_state"],
                row["completion_state"],
                row["object_key"],
                row["multipart_id"],
                row["declared_size_bytes"],
            )
        finally:
            await connection.close()

    asyncio.run(create_database())
    environment = os.environ.copy()
    environment["SUPERBOSS_DATABASE_URL"] = temporary_url
    try:
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "0003_unique_project_name"],
            cwd=SERVER_ROOT,
            env=environment,
            check=True,
        )
        baseline = asyncio.run(catalog())
        assert "files" not in baseline[0] and "uploads" not in baseline[0]
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "0005_file_lifecycle"],
            cwd=SERVER_ROOT,
            env=environment,
            check=True,
        )
        asyncio.run(insert_legacy_upload())

        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "0017_import_jobs"],
            cwd=SERVER_ROOT,
            env=environment,
            check=True,
        )
        tables, constraints, indexes = asyncio.run(catalog())
        assert asyncio.run(revision()) == "0017_import_jobs"
        assert "trg_files_snapshot_storage_cleanup" in asyncio.run(trigger_names())
        assert {"files", "uploads", "file_upload_lifecycle", "file_lifecycle_outbox", "file_storage_cleanup"} <= set(tables)
        assert _DEVICE_TABLES | _IMPORT_TABLES <= set(tables)
        assert asyncio.run(table_columns("files")) == [
            "id", "project_id", "filename", "category", "file_date", "object_key", "size_bytes",
            "sha256", "state", "uploader_kind", "uploader_id", "content_type", "scan_result",
            "created_at", "updated_at",
        ]
        assert asyncio.run(table_columns("uploads")) == [
            "id", "file_id", "project_id", "uploader_kind", "uploader_id", "idempotency_key",
            "metadata_fingerprint", "multipart_id", "created_at",
        ]
        assert asyncio.run(table_columns("file_upload_lifecycle")) == [
            "upload_id", "file_id", "project_id", "object_key", "multipart_id", "content_type",
            "declared_size_bytes", "provision_state", "completion_state", "parts_digest",
            "completion_event_key", "created_at", "updated_at", "canonical_parts_json",
            "completion_actor_kind", "completion_actor_id", "completion_actor_role",
            "completion_request_id", "prepared_at", "completion_attempt_count",
            "completion_next_attempt_at", "completion_last_error_code",
        ]
        assert asyncio.run(table_columns("file_lifecycle_outbox")) == [
            "id", "kind", "dedupe_key", "file_id", "project_id", "state", "attempt_count",
            "next_attempt_at", "locked_at", "last_error_code", "created_at", "updated_at",
            "claim_token",
        ]
        assert asyncio.run(table_columns("file_storage_cleanup")) == [
            "id", "operation", "dedupe_key", "object_key", "multipart_id", "lifecycle_id",
            "state", "attempt_count", "next_attempt_at", "locked_at", "last_error_code",
            "created_at", "updated_at", "claim_token",
        ]
        assert "event_key" in asyncio.run(table_columns("audit_logs"))

        definitions = {(table, name): definition for table, name, _kind, definition in constraints}
        assert definitions[("files", "files_project_id_fkey")] == "FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE"
        assert definitions[("uploads", "fk_uploads_file_project")] == (
            "FOREIGN KEY (file_id, project_id) REFERENCES files(id, project_id) ON DELETE CASCADE"
        )
        assert definitions[("uploads", "uploads_project_id_fkey")] == "FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE"
        assert {name for (table, name) in definitions if table == "files"} >= {
            "ck_files_state", "ck_files_size", "ck_files_sha256", "ck_files_uploader_kind",
            "ck_files_filename", "ck_files_category", "ck_files_object_key", "ck_files_content_type",
            "uq_files_id_project",
        }
        assert {name for (table, name) in definitions if table == "uploads"} >= {
            "ck_uploads_uploader_kind", "ck_uploads_idempotency_key", "ck_uploads_fingerprint",
            "ck_uploads_multipart_id_empty_or_nonempty", "uq_upload_idempotency",
        }
        assert {name for (table, name) in definitions if table == "file_upload_lifecycle"} >= {
            "pk_file_upload_lifecycle", "ck_file_upload_lifecycle_provision_state",
            "ck_file_upload_lifecycle_completion_state", "ck_file_upload_lifecycle_size",
            "ck_file_upload_lifecycle_object_key", "ck_file_upload_lifecycle_content_type",
            "ck_file_upload_lifecycle_multipart_id", "ck_file_upload_lifecycle_parts_digest",
            "ck_file_upload_lifecycle_completion_attempt_count",
            "ck_file_upload_lifecycle_completion_error_code",
        }
        assert {name for (table, name) in definitions if table == "file_lifecycle_outbox"} >= {
            "pk_file_lifecycle_outbox", "uq_file_lifecycle_outbox_kind_dedupe",
            "ck_file_lifecycle_outbox_kind", "ck_file_lifecycle_outbox_state",
            "ck_file_lifecycle_outbox_attempt_count",
        }
        assert {name for (table, name) in definitions if table == "file_storage_cleanup"} >= {
            "pk_file_storage_cleanup", "uq_file_storage_cleanup_operation_dedupe",
            "ck_file_storage_cleanup_operation", "ck_file_storage_cleanup_state",
            "ck_file_storage_cleanup_attempt_count", "ck_file_storage_cleanup_dedupe_key",
        }
        assert "DISCOVER_MULTIPART" in definitions[
            ("file_storage_cleanup", "ck_file_storage_cleanup_operation")
        ]
        assert "UPLOADING" in definitions[("files", "ck_files_state")] and "FAILED" in definitions[("files", "ck_files_state")]
        size_constraint = definitions[("files", "ck_files_size")]
        assert "size_bytes >= 1" in size_constraint and "size_bytes <= 104857600" in size_constraint
        assert "^[0-9a-f]{64}$" in definitions[("files", "ck_files_sha256")]
        multipart_constraint = definitions[("uploads", "ck_uploads_multipart_id_empty_or_nonempty")]
        assert "multipart_id" in multipart_constraint and "char_length" in multipart_constraint
        assert asyncio.run(lifecycle_backfill()) == (
            "READY", "NONE", f"projects/{legacy_project_id}/docs/2026-08-09/{legacy_file_id}/legacy.pdf",
            "legacy-multipart", 1,
        )
        normalized_indexes = "\n".join(index_definition.replace(" ", "") for _table, index_definition in indexes)
        assert "UNIQUEINDEXfiles_object_key_keyONpublic.filesUSINGbtree(object_key)" in normalized_indexes
        assert "UNIQUEINDEXuq_files_id_projectONpublic.filesUSINGbtree(id,project_id)" in normalized_indexes
        assert "UNIQUEINDEXuploads_file_id_keyONpublic.uploadsUSINGbtree(file_id)" in normalized_indexes
        assert "UNIQUE(project_id,uploader_kind,uploader_id,idempotency_key)" in definitions[("uploads", "uq_upload_idempotency")].replace(" ", "")
        assert "UNIQUEINDEXuq_audit_logs_event_keyONpublic.audit_logsUSINGbtree(event_key)WHERE(event_keyISNOTNULL)" in normalized_indexes
        assert "INDEXix_file_lifecycle_outbox_pendingONpublic.file_lifecycle_outboxUSINGbtree(state,next_attempt_at)" in normalized_indexes
        assert "INDEXix_file_lifecycle_outbox_due_leaseONpublic.file_lifecycle_outboxUSINGbtree(state,next_attempt_at,locked_at)" in normalized_indexes
        assert "INDEXix_file_storage_cleanup_pendingONpublic.file_storage_cleanupUSINGbtree(state,next_attempt_at)" in normalized_indexes
        assert "INDEXix_file_storage_cleanup_due_leaseONpublic.file_storage_cleanupUSINGbtree(state,next_attempt_at,locked_at)" in normalized_indexes
        assert "INDEXix_file_upload_lifecycle_completion_dueONpublic.file_upload_lifecycleUSINGbtree(completion_state,completion_next_attempt_at)" in normalized_indexes
        assert "UNIQUEINDEXuq_file_storage_cleanup_operation_target" in normalized_indexes

        subprocess.run(
            [sys.executable, "-m", "alembic", "downgrade", "0003_unique_project_name"],
            cwd=SERVER_ROOT,
            env=environment,
            check=True,
        )
        assert asyncio.run(catalog()) == baseline
        assert "files" not in asyncio.run(catalog())[0] and "uploads" not in asyncio.run(catalog())[0]
        assert asyncio.run(trigger_names()) == set()
    finally:
        asyncio.run(drop_database())


@pytest.mark.asyncio
async def test_database_rejects_upload_referencing_file_from_other_project(
    db_session: AsyncSession, active_owner: User
) -> None:
    """A cross-project Upload/File pair must be impossible even for direct database writers."""
    project_a = Project(name="File project A")
    project_b = Project(name="File project B")
    db_session.add_all([project_a, project_b])
    await db_session.flush()
    file = File(
        project_id=project_a.id,
        filename="a.pdf",
        category="资料",
        file_date=date(2026, 8, 9),
        object_key=f"projects/{project_a.id}/a.pdf",
        size_bytes=1,
        sha256="0" * 64,
        state=FileState.UPLOADING,
        uploader_id=active_owner.id,
        uploader_kind="user",
        content_type="application/pdf",
    )
    db_session.add(file)
    await db_session.flush()
    db_session.add(
        Upload(
            file_id=file.id,
            project_id=project_b.id,
            uploader_id=active_owner.id,
            uploader_kind="user",
            idempotency_key="cross-project",
            metadata_fingerprint="0" * 64,
            multipart_id="multipart-cross-project",
        )
    )

    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


def test_downgrade_blocks_live_file_lifecycle_without_data_loss(
    postgres_database: str,
) -> None:
    """A live upload must stop lifecycle-table DDL before any destructive downgrade."""
    temporary_name = f"superboss_downgrade_guard_{uuid4().hex}"
    admin_url = _database_url(postgres_database, "postgres")
    temporary_url = _database_url(postgres_database, temporary_name)
    project_id, file_id, upload_id = uuid4(), uuid4(), uuid4()

    async def create_database() -> None:
        connection = await asyncpg.connect(admin_url.replace("postgresql+asyncpg", "postgresql"))
        try:
            await connection.execute(f'CREATE DATABASE "{temporary_name}"')
        finally:
            await connection.close()

    async def drop_database() -> None:
        connection = await asyncpg.connect(admin_url.replace("postgresql+asyncpg", "postgresql"))
        try:
            await connection.execute(f'DROP DATABASE IF EXISTS "{temporary_name}" WITH (FORCE)')
        finally:
            await connection.close()

    async def seed() -> tuple[
        str,
        set[str],
        int,
        tuple[_CatalogFingerprint, _CatalogFingerprint],
    ]:
        connection = await asyncpg.connect(temporary_url.replace("postgresql+asyncpg", "postgresql"))
        try:
            object_key = f"projects/{project_id}/docs/2026-08-09/{file_id}/live.pdf"
            await connection.execute(
                "INSERT INTO projects (id, name, is_test, status) VALUES ($1, $2, FALSE, 'ACTIVE')",
                project_id,
                f"Downgrade guard {project_id.hex}",
            )
            await connection.execute(
                "INSERT INTO files (id, project_id, filename, category, file_date, object_key, "
                "size_bytes, sha256, state, uploader_kind, uploader_id, content_type) "
                "VALUES ($1, $2, 'live.pdf', 'docs', '2026-08-09', $3, 1, $4, 'UPLOADING', "
                "'user', $5, 'application/pdf')",
                file_id,
                project_id,
                object_key,
                "0" * 64,
                uuid4(),
            )
            await connection.execute(
                "INSERT INTO uploads (id, file_id, project_id, uploader_kind, uploader_id, "
                "idempotency_key, metadata_fingerprint, multipart_id) "
                "VALUES ($1, $2, $3, 'user', $4, 'live-downgrade', $5, NULL)",
                upload_id,
                file_id,
                project_id,
                uuid4(),
                "1" * 64,
            )
            await connection.execute(
                "INSERT INTO file_upload_lifecycle "
                "(upload_id, file_id, project_id, object_key, multipart_id, content_type, "
                "declared_size_bytes, provision_state, completion_state) "
                "VALUES ($1, $2, $3, $4, NULL, 'application/pdf', 1, 'PROVISIONING', 'NONE')",
                upload_id,
                file_id,
                project_id,
                object_key,
            )
            revision = str(await connection.fetchval("SELECT version_num FROM alembic_version"))
            tables = {
                row["tablename"]
                for row in await connection.fetch(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
                )
            }
            rows = int(
                await connection.fetchval(
                    "SELECT (SELECT count(*) FROM files) + (SELECT count(*) FROM uploads)"
                )
            )
            return revision, tables, rows, (
                await _device_catalog(connection),
                await _import_catalog(connection),
            )
        finally:
            await connection.close()

    async def snapshot() -> tuple[
        str,
        set[str],
        int,
        tuple[_CatalogFingerprint, _CatalogFingerprint],
    ]:
        connection = await asyncpg.connect(temporary_url.replace("postgresql+asyncpg", "postgresql"))
        try:
            revision = str(await connection.fetchval("SELECT version_num FROM alembic_version"))
            tables = {
                row["tablename"]
                for row in await connection.fetch(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
                )
            }
            rows = int(
                await connection.fetchval(
                    "SELECT (SELECT count(*) FROM files) + (SELECT count(*) FROM uploads)"
                )
            )
            return revision, tables, rows, (
                await _device_catalog(connection),
                await _import_catalog(connection),
            )
        finally:
            await connection.close()

    asyncio.run(create_database())
    environment = os.environ.copy()
    environment["SUPERBOSS_DATABASE_URL"] = temporary_url
    try:
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "0017_import_jobs"],
            cwd=SERVER_ROOT,
            env=environment,
            check=True,
        )
        before = asyncio.run(seed())
        assert before[0] == "0017_import_jobs"
        assert _DEVICE_TABLES | _IMPORT_TABLES <= before[1]
        assert all(before[3][0]) and all(before[3][1])
        downgrade = subprocess.run(
            [sys.executable, "-m", "alembic", "downgrade", "0005_file_lifecycle"],
            cwd=SERVER_ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        output = downgrade.stdout + downgrade.stderr
        assert downgrade.returncode != 0
        assert "SUPERBOSS_FILE_LIFECYCLE_DOWNGRADE_BLOCKED" in output
        after = asyncio.run(snapshot())
        assert after == before
        revision, tables, rows, _late_catalog = after
        assert revision == "0017_import_jobs"
        assert {
            "file_upload_lifecycle",
            "file_lifecycle_outbox",
            "file_storage_cleanup",
        } <= tables
        assert rows == 2
    finally:
        asyncio.run(drop_database())


def test_downgrade_guard_covers_each_live_lifecycle_shape(
    postgres_database: str,
) -> None:
    """Every live durable-work shape blocks downgrade before lifecycle DDL runs."""
    temporary_name = f"superboss_downgrade_matrix_{uuid4().hex}"
    admin_url = _database_url(postgres_database, "postgres")
    temporary_url = _database_url(postgres_database, temporary_name)

    async def create_database() -> None:
        connection = await asyncpg.connect(admin_url.replace("postgresql+asyncpg", "postgresql"))
        try:
            await connection.execute(f'CREATE DATABASE "{temporary_name}"')
        finally:
            await connection.close()

    async def drop_database() -> None:
        connection = await asyncpg.connect(admin_url.replace("postgresql+asyncpg", "postgresql"))
        try:
            await connection.execute(f'DROP DATABASE IF EXISTS "{temporary_name}" WITH (FORCE)')
        finally:
            await connection.close()

    async def reset() -> None:
        connection = await asyncpg.connect(temporary_url.replace("postgresql+asyncpg", "postgresql"))
        try:
            await connection.execute(
                "TRUNCATE file_lifecycle_outbox, file_storage_cleanup, file_upload_lifecycle, "
                "uploads, files, projects CASCADE"
            )
        finally:
            await connection.close()

    async def seed(shape: str) -> None:
        connection = await asyncpg.connect(temporary_url.replace("postgresql+asyncpg", "postgresql"))
        try:
            project_id, file_id, upload_id = uuid4(), uuid4(), uuid4()
            object_key = f"projects/{project_id}/docs/2026-08-09/{file_id}/{shape}.pdf"
            multipart_id = None if shape == "multipart_null" else f"multipart-{upload_id.hex}"
            provision_state = {
                "provisioning": "PROVISIONING",
                "cancel_requested": "CANCEL_REQUESTED",
            }.get(shape, "TERMINAL")
            completion_state = {
                "prepared": "PREPARED",
                "verified": "VERIFIED",
                "compensation_pending": "COMPENSATION_PENDING",
            }.get(shape, "QUARANTINED" if shape == "safe" else "NONE")
            await connection.execute(
                "INSERT INTO projects (id, name, is_test, status) VALUES ($1, $2, FALSE, 'ACTIVE')",
                project_id,
                f"Downgrade matrix {shape} {project_id.hex}",
            )
            await connection.execute(
                "INSERT INTO files (id, project_id, filename, category, file_date, object_key, "
                "size_bytes, sha256, state, uploader_kind, uploader_id, content_type) "
                "VALUES ($1, $2, 'matrix.pdf', 'docs', '2026-08-09', $3, 1, $4, 'UPLOADING', "
                "'user', $5, 'application/pdf')",
                file_id,
                project_id,
                object_key,
                "0" * 64,
                uuid4(),
            )
            await connection.execute(
                "INSERT INTO uploads (id, file_id, project_id, uploader_kind, uploader_id, "
                "idempotency_key, metadata_fingerprint, multipart_id) "
                "VALUES ($1, $2, $3, 'user', $4, $5, $6, $7)",
                upload_id,
                file_id,
                project_id,
                uuid4(),
                f"matrix-{shape}-{upload_id.hex}",
                "1" * 64,
                multipart_id,
            )
            await connection.execute(
                "INSERT INTO file_upload_lifecycle "
                "(upload_id, file_id, project_id, object_key, multipart_id, content_type, "
                "declared_size_bytes, provision_state, completion_state) "
                "VALUES ($1, $2, $3, $4, $5, 'application/pdf', 1, $6, $7)",
                upload_id,
                file_id,
                project_id,
                object_key,
                multipart_id,
                provision_state,
                completion_state,
            )
            if shape in {"outbox_pending", "safe"}:
                await connection.execute(
                    "INSERT INTO file_lifecycle_outbox "
                    "(id, kind, dedupe_key, file_id, project_id, state) "
                    "VALUES ($1, 'scan_dispatch', $2, $3, $4, $5)",
                    uuid4(),
                    uuid4(),
                    file_id,
                    project_id,
                    "DELIVERED" if shape == "safe" else "PENDING",
                )
            if shape in {"cleanup_pending", "safe"}:
                await connection.execute(
                    "INSERT INTO file_storage_cleanup "
                    "(id, operation, dedupe_key, object_key, multipart_id, lifecycle_id, state) "
                    "VALUES ($1, 'DELETE_OBJECT', $2, $3, NULL, $4, $5)",
                    uuid4(),
                    "2" * 64,
                    object_key,
                    upload_id,
                    "DONE" if shape == "safe" else "PENDING",
                )
            if shape == "safe":
                await connection.execute(
                    "INSERT INTO file_storage_cleanup "
                    "(id, operation, dedupe_key, object_key, multipart_id, lifecycle_id, state) "
                    "VALUES ($1, 'DISCOVER_MULTIPART', $2, $3, NULL, $4, 'DONE')",
                    uuid4(),
                    "3" * 64,
                    object_key,
                    upload_id,
                )
        finally:
            await connection.close()

    async def head_snapshot() -> tuple[
        str,
        set[str],
        set[str],
        int,
        tuple[_CatalogFingerprint, _CatalogFingerprint],
    ]:
        connection = await asyncpg.connect(temporary_url.replace("postgresql+asyncpg", "postgresql"))
        try:
            revision = str(await connection.fetchval("SELECT version_num FROM alembic_version"))
            tables = {
                row["tablename"]
                for row in await connection.fetch(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
                )
            }
            columns = {
                row["column_name"]
                for row in await connection.fetch(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'file_upload_lifecycle'"
                )
            }
            rows = int(
                await connection.fetchval(
                    "SELECT (SELECT count(*) FROM files) + (SELECT count(*) FROM uploads)"
                )
            )
            return revision, tables, columns, rows, (
                await _device_catalog(connection),
                await _import_catalog(connection),
            )
        finally:
            await connection.close()

    async def safe_downgrade_snapshot() -> tuple[str, set[str], int, int]:
        connection = await asyncpg.connect(temporary_url.replace("postgresql+asyncpg", "postgresql"))
        try:
            revision = str(await connection.fetchval("SELECT version_num FROM alembic_version"))
            tables = {
                row["tablename"]
                for row in await connection.fetch(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
                )
            }
            rows = int(
                await connection.fetchval(
                    "SELECT (SELECT count(*) FROM files) + (SELECT count(*) FROM uploads)"
                )
            )
            nonempty_multipart = int(
                await connection.fetchval(
                    "SELECT count(*) FROM uploads WHERE multipart_id IS NOT NULL"
                )
            )
            return revision, tables, rows, nonempty_multipart
        finally:
            await connection.close()

    def downgrade(environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "alembic", "downgrade", "0005_file_lifecycle"],
            cwd=SERVER_ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )

    asyncio.run(create_database())
    environment = os.environ.copy()
    environment["SUPERBOSS_DATABASE_URL"] = temporary_url
    unsafe_shapes = (
        "multipart_null",
        "provisioning",
        "cancel_requested",
        "prepared",
        "verified",
        "compensation_pending",
        "outbox_pending",
        "cleanup_pending",
    )
    try:
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "0017_import_jobs"],
            cwd=SERVER_ROOT,
            env=environment,
            check=True,
        )
        for shape in unsafe_shapes:
            asyncio.run(reset())
            asyncio.run(seed(shape))
            before = asyncio.run(head_snapshot())
            assert before[0] == "0017_import_jobs"
            assert _DEVICE_TABLES | _IMPORT_TABLES <= before[1]
            assert all(before[4][0]) and all(before[4][1])
            result = downgrade(environment)
            assert result.returncode != 0
            assert "SUPERBOSS_FILE_LIFECYCLE_DOWNGRADE_BLOCKED" in (
                result.stdout + result.stderr
            )
            after = asyncio.run(head_snapshot())
            assert after == before
            revision, tables, columns, rows, _late_catalog = after
            assert revision == "0017_import_jobs"
            assert {
                "file_upload_lifecycle",
                "file_lifecycle_outbox",
                "file_storage_cleanup",
            } <= tables
            assert "completion_next_attempt_at" in columns
            assert rows == 2

        asyncio.run(reset())
        asyncio.run(seed("safe"))
        result = downgrade(environment)
        assert result.returncode == 0, result.stdout + result.stderr
        revision, tables, rows, nonempty_multipart = asyncio.run(safe_downgrade_snapshot())
        assert revision == "0005_file_lifecycle"
        assert not {
            "file_upload_lifecycle",
            "file_lifecycle_outbox",
            "file_storage_cleanup",
        } & tables
        assert not _DEVICE_TABLES & tables
        assert not _IMPORT_TABLES & tables
        assert rows == 2 and nonempty_multipart == 1
    finally:
        asyncio.run(drop_database())
