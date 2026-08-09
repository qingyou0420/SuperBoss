"""Pristine PostgreSQL migration coverage for resumable file persistence."""

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import asyncpg

SERVER_ROOT = Path(__file__).resolve().parents[2]


def _database_url(database_url: str, database_name: str) -> str:
    parsed = urlsplit(database_url)
    return urlunsplit((parsed.scheme, parsed.netloc, f"/{database_name}", "", ""))


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
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=SERVER_ROOT,
            env=environment,
            check=True,
        )
        tables, constraints, indexes = asyncio.run(catalog())
        assert asyncio.run(revision()) == "0004_files_and_uploads"
        assert "files" in tables and "uploads" in tables
        assert asyncio.run(table_columns("files")) == [
            "id", "project_id", "filename", "category", "file_date", "object_key", "size_bytes",
            "sha256", "state", "uploader_kind", "uploader_id", "content_type", "scan_result",
            "created_at", "updated_at",
        ]
        assert asyncio.run(table_columns("uploads")) == [
            "id", "file_id", "project_id", "uploader_kind", "uploader_id", "idempotency_key",
            "metadata_fingerprint", "multipart_id", "created_at",
        ]

        definitions = {(table, name): definition for table, name, _kind, definition in constraints}
        assert definitions[("files", "files_project_id_fkey")] == "FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE"
        assert definitions[("uploads", "uploads_file_id_fkey")] == "FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE"
        assert definitions[("uploads", "uploads_project_id_fkey")] == "FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE"
        assert {name for (table, name) in definitions if table == "files"} >= {
            "ck_files_state", "ck_files_size", "ck_files_sha256", "ck_files_uploader_kind",
            "ck_files_filename", "ck_files_category", "ck_files_object_key", "ck_files_content_type",
        }
        assert {name for (table, name) in definitions if table == "uploads"} >= {
            "ck_uploads_uploader_kind", "ck_uploads_idempotency_key", "ck_uploads_fingerprint",
            "ck_uploads_multipart_id_not_empty", "uq_upload_idempotency",
        }
        assert "UPLOADING" in definitions[("files", "ck_files_state")] and "FAILED" in definitions[("files", "ck_files_state")]
        size_constraint = definitions[("files", "ck_files_size")]
        assert "size_bytes >= 1" in size_constraint and "size_bytes <= 104857600" in size_constraint
        assert "^[0-9a-f]{64}$" in definitions[("files", "ck_files_sha256")]
        multipart_constraint = definitions[("uploads", "ck_uploads_multipart_id_not_empty")]
        assert "char_length" in multipart_constraint and "multipart_id" in multipart_constraint and "> 0" in multipart_constraint
        normalized_indexes = "\n".join(index_definition.replace(" ", "") for _table, index_definition in indexes)
        assert "UNIQUEINDEXfiles_object_key_keyONpublic.filesUSINGbtree(object_key)" in normalized_indexes
        assert "UNIQUEINDEXuploads_file_id_keyONpublic.uploadsUSINGbtree(file_id)" in normalized_indexes
        assert "UNIQUE(project_id,uploader_kind,uploader_id,idempotency_key)" in definitions[("uploads", "uq_upload_idempotency")].replace(" ", "")

        subprocess.run(
            [sys.executable, "-m", "alembic", "downgrade", "0003_unique_project_name"],
            cwd=SERVER_ROOT,
            env=environment,
            check=True,
        )
        assert asyncio.run(catalog()) == baseline
        assert "files" not in asyncio.run(catalog())[0] and "uploads" not in asyncio.run(catalog())[0]
    finally:
        asyncio.run(drop_database())
