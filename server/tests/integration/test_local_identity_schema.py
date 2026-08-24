"""PostgreSQL contract for the local-password identity cutover."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy import inspect

from superboss.modules.auth import models as auth_models
from superboss.modules.users.models import User

SERVER_ROOT = Path(__file__).resolve().parents[2]
LOCAL_COLUMNS = {
    "id",
    "username",
    "display_name",
    "password_hash",
    "must_change_password",
    "password_changed_at",
    "failed_login_count",
    "locked_until",
    "role",
    "status",
    "last_login_at",
    "created_at",
    "updated_at",
}


def _database_url(database_url: str, database_name: str) -> str:
    parsed = urlsplit(database_url)
    return urlunsplit((parsed.scheme, parsed.netloc, f"/{database_name}", "", ""))


def _asyncpg_url(database_url: str) -> str:
    return database_url.replace("postgresql+asyncpg", "postgresql")


def _alembic(database_url: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["SUPERBOSS_DATABASE_URL"] = database_url
    return subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        cwd=SERVER_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )


async def _with_temporary_database(
    database_url: str,
    operation: Callable[[str], Awaitable[None]],
) -> None:
    name = f"superboss_local_identity_{uuid4().hex}"
    admin_url = _asyncpg_url(_database_url(database_url, "postgres"))
    temporary_url = _database_url(database_url, name)
    connection = await asyncpg.connect(admin_url)
    try:
        await connection.execute(f'CREATE DATABASE "{name}"')
    finally:
        await connection.close()
    try:
        await operation(temporary_url)
    finally:
        connection = await asyncpg.connect(admin_url)
        try:
            await connection.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        finally:
            await connection.close()


async def _catalog(database_url: str) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    connection = await asyncpg.connect(_asyncpg_url(database_url))
    try:
        columns = await connection.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='users' ORDER BY column_name"
        )
        constraints = await connection.fetch(
            "SELECT conname FROM pg_constraint "
            "WHERE conrelid='users'::regclass ORDER BY conname"
        )
        tables = await connection.fetch(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
        )
        return (
            tuple(row["column_name"] for row in columns),
            tuple(row["conname"] for row in constraints),
            tuple(row["tablename"] for row in tables),
        )
    finally:
        await connection.close()


def test_orm_exposes_only_local_identity_columns() -> None:
    assert set(inspect(User).columns.keys()) == LOCAL_COLUMNS
    assert not hasattr(auth_models, "OAuthState")
    assert "wecom_userid" not in User.__table__.columns


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("username", "Owner"),
        ("username", "ab"),
        ("username", "1owner"),
        ("username", "owner "),
        ("username", "用户账号"),
        ("password_hash", "not-an-argon2id-hash"),
        ("failed_login_count", -1),
    ],
)
def test_database_rejects_invalid_local_identity_values(
    postgres_database: str, column: str, value: object
) -> None:
    async def operation(database_url: str) -> None:
        assert _alembic(database_url, "upgrade", "head").returncode == 0
        values: dict[str, object] = {
            "id": uuid4(),
            "username": "owner",
            "display_name": "Owner",
            "password_hash": "$argon2id$v=19$m=19456,t=2,p=1$fixture$fixture",
            "password_changed_at": datetime(2026, 8, 11, tzinfo=UTC),
            "failed_login_count": 0,
            "role": "OWNER",
            "status": "ACTIVE",
        }
        values[column] = value
        connection = await asyncpg.connect(_asyncpg_url(database_url))
        try:
            with pytest.raises(asyncpg.CheckViolationError):
                await connection.execute(
                    "INSERT INTO users(id,username,display_name,password_hash,"
                    "password_changed_at,failed_login_count,role,status) "
                    "VALUES($1,$2,$3,$4,$5,$6,$7,$8)",
                    values["id"],
                    values["username"],
                    values["display_name"],
                    values["password_hash"],
                    values["password_changed_at"],
                    values["failed_login_count"],
                    values["role"],
                    values["status"],
                )
        finally:
            await connection.close()

    asyncio.run(_with_temporary_database(postgres_database, operation))
