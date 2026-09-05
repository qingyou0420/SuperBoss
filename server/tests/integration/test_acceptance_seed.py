"""Real-PostgreSQL acceptance seed contracts for local identities."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import asyncpg
import pytest

from superboss.modules.auth.passwords import verify_password

SERVER_ROOT = Path(__file__).resolve().parents[2]
SEED_SCRIPT = SERVER_ROOT / "scripts" / "seed_acceptance.py"
NORMAL_PROJECT_NAME = "M1 正常项目"
TEST_PROJECT_NAME = "验收测试"
OWNER_PASSWORD = "acceptance owner local password"
STAFF_PASSWORD = "acceptance staff local password"


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("seed_acceptance", SEED_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _asyncpg_url(database_url: str) -> str:
    return database_url.replace("postgresql+asyncpg", "postgresql")


def _run(coroutine: Any) -> Any:
    return asyncio.run(coroutine)


async def _execute(database_url: str, statement: str, *arguments: object) -> str:
    connection = await asyncpg.connect(_asyncpg_url(database_url))
    try:
        return await connection.execute(statement, *arguments)
    finally:
        await connection.close()


async def _fetch(database_url: str, statement: str, *arguments: object) -> list[asyncpg.Record]:
    connection = await asyncpg.connect(_asyncpg_url(database_url))
    try:
        return await connection.fetch(statement, *arguments)
    finally:
        await connection.close()


def _cleanup(database_url: str, usernames: list[str]) -> None:
    async def cleanup() -> None:
        connection = await asyncpg.connect(_asyncpg_url(database_url))
        try:
            async with connection.transaction():
                await connection.execute(
                    "DELETE FROM projects WHERE name IN ($1, $2)",
                    NORMAL_PROJECT_NAME,
                    TEST_PROJECT_NAME,
                )
                await connection.execute(
                    "DELETE FROM users WHERE username = ANY($1::text[])", usernames
                )
        finally:
            await connection.close()

    _run(cleanup())


def _reader(*values: str) -> Callable[[str], str]:
    remaining = iter(values)
    return lambda _prompt: next(remaining)


def test_seed_empty_database_and_repeat_are_idempotent(postgres_database: str) -> None:
    module = _module()
    owner, staff = "acceptance-owner", "acceptance-staff"
    _cleanup(postgres_database, [owner, staff])
    try:
        first = _run(
            module.seed(
                postgres_database, owner, OWNER_PASSWORD, staff, STAFF_PASSWORD
            )
        )
        second = _run(
            module.seed(
                postgres_database, owner, OWNER_PASSWORD, staff, STAFF_PASSWORD
            )
        )

        assert first == second
        assert set(first.__dict__) == {
            "normal_project_id",
            "owner_id",
            "staff_id",
            "test_project_id",
        }
        rows = _run(
            _fetch(
                postgres_database,
                "SELECT username, role, password_hash, must_change_password "
                "FROM users WHERE username = ANY($1::text[]) ORDER BY username",
                [owner, staff],
            )
        )
        assert [(row["username"], row["role"]) for row in rows] == [
            (owner, "OWNER"),
            (staff, "STAFF"),
        ]
        assert verify_password(rows[0]["password_hash"], OWNER_PASSWORD).valid
        assert verify_password(rows[1]["password_hash"], STAFF_PASSWORD).valid
        assert all(row["must_change_password"] is False for row in rows)
        assert OWNER_PASSWORD not in repr(first) and STAFF_PASSWORD not in repr(first)
    finally:
        _cleanup(postgres_database, [owner, staff])


def test_seed_rolls_back_users_on_project_conflict(postgres_database: str) -> None:
    module = _module()
    owner, staff = "rollback-owner", "rollback-staff"
    _cleanup(postgres_database, [owner, staff])
    _run(
        _execute(
            postgres_database,
            "INSERT INTO projects (id,name,description,is_test,status,stage,progress_percent) "
            "VALUES (gen_random_uuid(),$1,'',false,'ACTIVE','PLANNING',0)",
            NORMAL_PROJECT_NAME.lower(),
        )
    )
    try:
        with pytest.raises(module.SeedRefusedError):
            _run(
                module.seed(
                    postgres_database, owner, OWNER_PASSWORD, staff, STAFF_PASSWORD
                )
            )
        assert _run(
            _fetch(
                postgres_database,
                "SELECT username FROM users WHERE username = ANY($1::text[])",
                [owner, staff],
            )
        ) == []
    finally:
        _run(
            _execute(
                postgres_database,
                "DELETE FROM projects WHERE lower(name)=lower($1)",
                NORMAL_PROJECT_NAME,
            )
        )
        _cleanup(postgres_database, [owner, staff])


def test_seed_refuses_production_before_reading_passwords(
    postgres_database: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    monkeypatch.setenv("SUPERBOSS_DATABASE_URL", postgres_database)
    monkeypatch.setenv("SUPERBOSS_ENVIRONMENT", "production")
    monkeypatch.setenv("SUPERBOSS_OWNER_USERNAME", "production-owner")
    monkeypatch.setenv("SUPERBOSS_ACCEPTANCE_STAFF_USERNAME", "production-staff")
    calls = 0

    def forbidden_reader(_prompt: str) -> str:
        nonlocal calls
        calls += 1
        return OWNER_PASSWORD

    with pytest.raises(module.SeedRefusedError):
        _run(module.run_from_environment(False, forbidden_reader))
    assert calls == 0


def test_seed_preserves_existing_owner_and_rejects_username_mismatch(
    postgres_database: str,
) -> None:
    module = _module()
    existing, requested, staff = "existing-owner", "requested-owner", "guard-staff"
    _cleanup(postgres_database, [existing, requested, staff])
    password_hash = module.hash_password(OWNER_PASSWORD)
    _run(
        _execute(
            postgres_database,
            "INSERT INTO users (id,username,display_name,password_hash,must_change_password,"
            "password_changed_at,role,status) VALUES "
            "(gen_random_uuid(),$1,'Keep exactly',$2,false,clock_timestamp(),'OWNER','DISABLED')",
            existing,
            password_hash,
        )
    )
    try:
        with pytest.raises(module.SeedRefusedError):
            _run(
                module.seed(
                    postgres_database,
                    requested,
                    OWNER_PASSWORD,
                    staff,
                    STAFF_PASSWORD,
                )
            )
        rows = _run(
            _fetch(
                postgres_database,
                "SELECT username,display_name,password_hash,status FROM users WHERE role='OWNER'",
            )
        )
        assert [dict(row) for row in rows] == [
            {
                "username": existing,
                "display_name": "Keep exactly",
                "password_hash": password_hash,
                "status": "DISABLED",
            }
        ]
    finally:
        _cleanup(postgres_database, [existing, requested, staff])


def test_seed_reuses_matching_owner_without_modifying_credentials(
    postgres_database: str,
) -> None:
    module = _module()
    owner, staff = "preserved-owner", "preserved-staff"
    _cleanup(postgres_database, [owner, staff])
    original_hash = module.hash_password("different preserved owner password")
    _run(
        _execute(
            postgres_database,
            "INSERT INTO users (id,username,display_name,password_hash,must_change_password,"
            "password_changed_at,role,status) VALUES "
            "(gen_random_uuid(),$1,'Keep exactly',$2,true,clock_timestamp(),'OWNER','DISABLED')",
            owner,
            original_hash,
        )
    )
    try:
        _run(
            module.seed(
                postgres_database, owner, OWNER_PASSWORD, staff, STAFF_PASSWORD
            )
        )
        row = _run(
            _fetch(
                postgres_database,
                "SELECT display_name,password_hash,must_change_password,status "
                "FROM users WHERE username=$1",
                owner,
            )
        )[0]
        assert dict(row) == {
            "display_name": "Keep exactly",
            "password_hash": original_hash,
            "must_change_password": True,
            "status": "DISABLED",
        }
    finally:
        _cleanup(postgres_database, [owner, staff])


def test_cli_has_no_password_arguments_or_password_environment_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    parser = module.build_parser()
    options = {
        option for action in parser._actions for option in action.option_strings
    }
    assert "--password" not in options and "--password-file" not in options
    monkeypatch.setenv("SUPERBOSS_OWNER_PASSWORD", "forbidden environment secret")
    monkeypatch.setenv("SUPERBOSS_ACCEPTANCE_STAFF_PASSWORD", "forbidden staff secret")
    result = _run(
        module._read_passwords(
            _reader(OWNER_PASSWORD, OWNER_PASSWORD, STAFF_PASSWORD, STAFF_PASSWORD)
        )
    )
    assert result == (OWNER_PASSWORD, STAFF_PASSWORD)
