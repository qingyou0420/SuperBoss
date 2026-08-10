"""Real-PostgreSQL acceptance seed contracts."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from uuid import UUID

import asyncpg

SERVER_ROOT = Path(__file__).resolve().parents[2]
SEED_SCRIPT = SERVER_ROOT / "scripts" / "seed_acceptance.py"
NORMAL_PROJECT_NAME = "M1 正常项目"
TEST_PROJECT_NAME = "验收测试"


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


def _environment(database_url: str, owner_userid: str, staff_userid: str) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "SUPERBOSS_DATABASE_URL": database_url,
            "SUPERBOSS_ENVIRONMENT": "test",
            "SUPERBOSS_OWNER_WECOM_USERID": owner_userid,
            "SUPERBOSS_ACCEPTANCE_STAFF_WECOM_USERID": staff_userid,
        }
    )
    return environment


def _seed(environment: dict[str, str], *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SEED_SCRIPT), *arguments],
        cwd=SERVER_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def _assert_safe_failure(result: subprocess.CompletedProcess[str], secrets: list[str]) -> None:
    assert result.returncode != 0
    assert result.stdout == ""
    combined = result.stderr
    for secret in secrets:
        assert secret not in combined


def _cleanup(database_url: str, userids: list[str]) -> None:
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
                    "DELETE FROM users WHERE wecom_userid = ANY($1::text[])", userids
                )
        finally:
            await connection.close()

    _run(cleanup())


def test_seed_empty_database_and_repeat_are_idempotent(postgres_database: str) -> None:
    """Changing find-or-create to unconditional inserts would break the second run."""
    owner = "acceptance-test-owner-empty"
    staff = "acceptance-test-staff-empty"
    environment = _environment(postgres_database, owner, staff)
    _cleanup(postgres_database, [owner, staff])
    try:
        first = _seed(environment)
        second = _seed(environment)

        assert first.returncode == second.returncode == 0
        first_ids = json.loads(first.stdout)
        second_ids = json.loads(second.stdout)
        assert first_ids == second_ids
        assert set(first_ids) == {
            "normal_project_id",
            "owner_id",
            "staff_id",
            "test_project_id",
        }
        assert all(str(UUID(value)) == value for value in first_ids.values())
        assert first.stderr == second.stderr == ""

        rows = _run(
            _fetch(
                postgres_database,
                """
                SELECT 'user' AS kind, wecom_userid AS name, role AS marker
                FROM users WHERE wecom_userid = ANY($1::text[])
                UNION ALL
                SELECT 'project', name, CASE WHEN is_test THEN 'test' ELSE 'normal' END
                FROM projects WHERE name IN ($2, $3)
                ORDER BY kind, name
                """,
                [owner, staff],
                NORMAL_PROJECT_NAME,
                TEST_PROJECT_NAME,
            )
        )
        assert [(row["kind"], row["name"], row["marker"]) for row in rows] == [
            ("project", NORMAL_PROJECT_NAME, "normal"),
            ("project", TEST_PROJECT_NAME, "test"),
            ("user", owner, "OWNER"),
            ("user", staff, "STAFF"),
        ]
    finally:
        _cleanup(postgres_database, [owner, staff])


def test_seed_rolls_back_all_records_on_case_insensitive_project_conflict(
    postgres_database: str,
) -> None:
    """Removing the single transaction would leave users behind after a project conflict."""
    owner = "acceptance-test-owner-rollback"
    staff = "acceptance-test-staff-rollback"
    environment = _environment(postgres_database, owner, staff)
    _cleanup(postgres_database, [owner, staff])
    _run(
        _execute(
            postgres_database,
            "INSERT INTO projects (id, name, is_test, status) VALUES (gen_random_uuid(), $1, false, 'ACTIVE')",
            NORMAL_PROJECT_NAME.lower(),
        )
    )
    try:
        result = _seed(environment)
        _assert_safe_failure(result, [owner, staff, postgres_database])
        rows = _run(
            _fetch(
                postgres_database,
                "SELECT wecom_userid FROM users WHERE wecom_userid = ANY($1::text[])",
                [owner, staff],
            )
        )
        assert rows == []
    finally:
        _run(
            _execute(
                postgres_database,
                "DELETE FROM projects WHERE lower(name) = lower($1)",
                NORMAL_PROJECT_NAME,
            )
        )
        _cleanup(postgres_database, [owner, staff])


def test_seed_refuses_production_without_confirmation(postgres_database: str) -> None:
    """Removing the production guard would create acceptance identities in production."""
    owner = "acceptance-test-owner-production"
    staff = "acceptance-test-staff-production"
    environment = _environment(postgres_database, owner, staff)
    environment["SUPERBOSS_ENVIRONMENT"] = "production"
    _cleanup(postgres_database, [owner, staff])
    try:
        result = _seed(environment)
        _assert_safe_failure(result, [owner, staff, postgres_database])
        rows = _run(
            _fetch(
                postgres_database,
                "SELECT wecom_userid FROM users WHERE wecom_userid = ANY($1::text[])",
                [owner, staff],
            )
        )
        assert rows == []
    finally:
        _cleanup(postgres_database, [owner, staff])


def test_seed_never_changes_existing_owner_and_fails_closed_on_userid_mismatch(
    postgres_database: str,
) -> None:
    """Changing the OWNER lookup to update-or-create would mutate the protected account."""
    existing_owner = "acceptance-test-owner-existing"
    requested_owner = "acceptance-test-owner-requested"
    staff = "acceptance-test-staff-owner-guard"
    environment = _environment(postgres_database, requested_owner, staff)
    _cleanup(postgres_database, [existing_owner, requested_owner, staff])
    _run(
        _execute(
            postgres_database,
            """
            INSERT INTO users (id, wecom_userid, display_name, role, status)
            VALUES (gen_random_uuid(), $1, 'Protected display', 'OWNER', 'DISABLED')
            """,
            existing_owner,
        )
    )
    try:
        result = _seed(environment)
        _assert_safe_failure(
            result, [existing_owner, requested_owner, staff, postgres_database]
        )
        rows = _run(
            _fetch(
                postgres_database,
                "SELECT wecom_userid, display_name, role, status FROM users WHERE role = 'OWNER'",
            )
        )
        assert [dict(row) for row in rows] == [
            {
                "wecom_userid": existing_owner,
                "display_name": "Protected display",
                "role": "OWNER",
                "status": "DISABLED",
            }
        ]
    finally:
        _cleanup(postgres_database, [existing_owner, requested_owner, staff])


def test_seed_reuses_matching_owner_without_modifying_it(postgres_database: str) -> None:
    """Changing idempotency to normalize OWNER fields would violate OWNER immutability."""
    owner = "acceptance-test-owner-preserve"
    staff = "acceptance-test-staff-preserve"
    environment = _environment(postgres_database, owner, staff)
    _cleanup(postgres_database, [owner, staff])
    _run(
        _execute(
            postgres_database,
            """
            INSERT INTO users (id, wecom_userid, display_name, role, status)
            VALUES (gen_random_uuid(), $1, 'Keep exactly', 'OWNER', 'DISABLED')
            """,
            owner,
        )
    )
    try:
        result = _seed(environment)
        assert result.returncode == 0
        row = _run(
            _fetch(
                postgres_database,
                "SELECT display_name, role, status FROM users WHERE wecom_userid = $1",
                owner,
            )
        )[0]
        assert dict(row) == {
            "display_name": "Keep exactly",
            "role": "OWNER",
            "status": "DISABLED",
        }
    finally:
        _cleanup(postgres_database, [owner, staff])
