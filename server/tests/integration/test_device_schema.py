"""PostgreSQL migration contracts for Kimi device credentials."""

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import asyncpg

SERVER_ROOT = Path(__file__).resolve().parents[2]
DEVICE_TABLES = {
    "device_connections",
    "device_sessions",
    "device_project_grants",
    "device_scope_grants",
    "device_pairing_codes",
    "device_pairing_projects",
}


def _database_url(database_url: str, database_name: str) -> str:
    parsed = urlsplit(database_url)
    return urlunsplit((parsed.scheme, parsed.netloc, f"/{database_name}", "", ""))


def test_0016_round_trip_and_guard_are_atomic(postgres_database: str) -> None:
    """Missing 0016 or a late downgrade guard would lose credential state and catalog."""
    temporary_name = f"superboss_devices_{uuid4().hex}"
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
            await connection.execute(f'DROP DATABASE IF EXISTS "{temporary_name}" WITH (FORCE)')
        finally:
            await connection.close()

    async def snapshot() -> tuple[
        str,
        set[str],
        dict[str, int],
        tuple[tuple[str, str, str, str], ...],
        tuple[tuple[str, str, str, str], ...],
        tuple[tuple[str, str, str], ...],
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
            counts = {
                table: int(await connection.fetchval(f'SELECT count(*) FROM "{table}"'))
                for table in DEVICE_TABLES & tables
            }
            columns = await connection.fetch(
                "SELECT table_name, column_name, data_type, is_nullable "
                "FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = ANY($1::text[]) "
                "ORDER BY table_name, ordinal_position",
                list(DEVICE_TABLES),
            )
            constraints = await connection.fetch(
                "SELECT relation.relname AS table_name, constraint_.conname, "
                "constraint_.contype, pg_get_constraintdef(constraint_.oid) AS definition "
                "FROM pg_constraint AS constraint_ "
                "JOIN pg_class AS relation ON relation.oid = constraint_.conrelid "
                "JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
                "WHERE namespace.nspname = 'public' AND relation.relname = ANY($1::text[]) "
                "ORDER BY relation.relname, constraint_.conname",
                list(DEVICE_TABLES),
            )
            indexes = await connection.fetch(
                "SELECT tablename, indexname, indexdef FROM pg_indexes "
                "WHERE schemaname = 'public' AND tablename = ANY($1::text[]) "
                "ORDER BY tablename, indexname",
                list(DEVICE_TABLES),
            )
            return (
                revision,
                tables,
                counts,
                tuple(tuple(row) for row in columns),
                tuple(tuple(row) for row in constraints),
                tuple(tuple(row) for row in indexes),
            )
        finally:
            await connection.close()

    async def seed_pairing_state() -> None:
        connection = await asyncpg.connect(pg_url)
        owner_id, project_id, code_id = uuid4(), uuid4(), uuid4()
        try:
            await connection.execute(
                "INSERT INTO users (id, wecom_userid, display_name, role, status) "
                "VALUES ($1, $2, 'Owner', 'OWNER', 'ACTIVE')",
                owner_id,
                f"owner-{owner_id}",
            )
            await connection.execute(
                "INSERT INTO projects (id, name, is_test, status) VALUES ($1, $2, FALSE, 'ACTIVE')",
                project_id,
                f"Device project {project_id}",
            )
            await connection.execute(
                "INSERT INTO device_pairing_codes (id, owner_id, code_hash, expires_at) "
                "VALUES ($1, $2, $3, clock_timestamp() + interval '10 minutes')",
                code_id,
                owner_id,
                "a" * 64,
            )
            await connection.execute(
                "INSERT INTO device_pairing_projects (pairing_code_id, project_id) VALUES ($1, $2)",
                code_id,
                project_id,
            )
        finally:
            await connection.close()

    environment = os.environ.copy()
    environment["SUPERBOSS_DATABASE_URL"] = temporary_url
    asyncio.run(create_database())
    try:
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "0015_discover_unbound_multipart"],
            cwd=SERVER_ROOT,
            env=environment,
            check=True,
        )
        subprocess.run(
            [
                sys.executable,
                "-m",
                "alembic",
                "upgrade",
                "0016_device_connections",
            ],
            cwd=SERVER_ROOT,
            env=environment,
            check=True,
        )
        revision, tables, counts, columns, constraints, indexes = asyncio.run(snapshot())
        assert revision == "0016_device_connections"
        assert DEVICE_TABLES <= tables
        assert all(count == 0 for count in counts.values())
        assert {row[0] for row in columns} == DEVICE_TABLES
        assert {row[0] for row in constraints} == DEVICE_TABLES
        assert {row[0] for row in indexes} == DEVICE_TABLES

        subprocess.run(
            [sys.executable, "-m", "alembic", "downgrade", "0015_discover_unbound_multipart"],
            cwd=SERVER_ROOT,
            env=environment,
            check=True,
        )
        revision, tables, _counts, _columns, _constraints, _indexes = asyncio.run(snapshot())
        assert revision == "0015_discover_unbound_multipart"
        assert not DEVICE_TABLES & tables
        subprocess.run(
            [
                sys.executable,
                "-m",
                "alembic",
                "upgrade",
                "0016_device_connections",
            ],
            cwd=SERVER_ROOT,
            env=environment,
            check=True,
        )

        asyncio.run(seed_pairing_state())
        before = asyncio.run(snapshot())
        downgrade = subprocess.run(
            [sys.executable, "-m", "alembic", "downgrade", "0015_discover_unbound_multipart"],
            cwd=SERVER_ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        assert downgrade.returncode != 0
        assert "SUPERBOSS_DEVICE_DOWNGRADE_BLOCKED" in downgrade.stdout + downgrade.stderr
        assert asyncio.run(snapshot()) == before
    finally:
        asyncio.run(drop_database())
