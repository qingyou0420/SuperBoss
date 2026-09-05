"""Server-local OWNER bootstrap and password recovery contracts."""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from superboss.modules.audit.models import AuditLog
from superboss.modules.auth.models import AuthSession
from superboss.modules.auth.passwords import verify_password
from superboss.modules.users.models import Role, User

INITIAL_PASSWORD = "owner initial moonlight phrase"
REPLACEMENT_PASSWORD = "owner replacement forest phrase"


def _admin() -> ModuleType:
    spec = importlib.util.find_spec("superboss.modules.auth.admin")
    assert spec is not None, "local OWNER administration module is required"
    return importlib.import_module("superboss.modules.auth.admin")


def _reader(*values: str):
    remaining = iter(values)

    def read(_prompt: str) -> str:
        return next(remaining)

    return read


def test_cli_parser_has_no_password_argument() -> None:
    parser = _admin().build_parser()
    options = {
        option
        for action in parser._actions
        for option in action.option_strings
    }
    assert "--password" not in options
    assert "--password-file" not in options
    assert parser.parse_args(["bootstrap", "--username", "owner", "--display-name", "Owner"])


@pytest.mark.asyncio
async def test_bootstrap_works_in_a_fresh_process_without_fixture_model_imports(
    postgres_database: str,
) -> None:
    engine = create_async_engine(postgres_database)
    async with engine.begin() as connection:
        await connection.execute(delete(AuditLog))
        await connection.execute(delete(User))
    environment = os.environ.copy()
    environment["SUPERBOSS_DATABASE_URL"] = postgres_database
    code = f"""
import asyncio
from superboss.core.db import async_session_factory
from superboss.modules.auth.admin import bootstrap_owner

result = asyncio.run(
    bootstrap_owner(
        async_session_factory(),
        username="owner",
        display_name="Owner",
        password_reader=lambda _prompt: {INITIAL_PASSWORD!r},
    )
)
print(result.user_id)
"""

    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[2],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    try:
        assert result.returncode == 0, result.stderr
        async with AsyncSession(engine, expire_on_commit=False) as session:
            owner = await session.scalar(select(User).where(User.username == "owner"))
            assert owner is not None
            event = await session.scalar(
                select(AuditLog).where(AuditLog.action == "auth.owner.bootstrap")
            )
            assert event is not None and event.actor_id == owner.id
    finally:
        async with engine.begin() as connection:
            await connection.execute(delete(AuditLog))
            await connection.execute(delete(User))
        await engine.dispose()


@pytest.mark.asyncio
async def test_bootstrap_creates_exactly_one_owner_and_secret_free_audit(
    db_session: AsyncSession,
) -> None:
    admin = _admin()
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    result = await admin.bootstrap_owner(
        factory,
        username="owner",
        display_name="Owner",
        password_reader=_reader(INITIAL_PASSWORD, INITIAL_PASSWORD),
    )

    db_session.expire_all()
    users = (await db_session.scalars(select(User))).all()
    assert len(users) == 1
    owner = users[0]
    assert (owner.id, owner.username, owner.display_name, owner.role) == (
        result.user_id,
        "owner",
        "Owner",
        Role.OWNER,
    )
    assert owner.must_change_password is False
    assert verify_password(owner.password_hash, INITIAL_PASSWORD).valid
    event = await db_session.scalar(select(AuditLog).where(AuditLog.action == "auth.owner.bootstrap"))
    assert event is not None
    assert (event.actor_id, event.object_id, event.outcome, event.metadata_json) == (
        owner.id,
        owner.id,
        "SUCCESS",
        {"actor_role": "OWNER"},
    )
    evidence = f"{result!r}{event.metadata_json!r}"
    assert INITIAL_PASSWORD not in evidence and owner.password_hash not in evidence


@pytest.mark.asyncio
async def test_bootstrap_refuses_existing_user_and_mismatched_confirmation(
    db_session: AsyncSession,
) -> None:
    admin = _admin()
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    with pytest.raises(admin.LocalAdminError):
        await admin.bootstrap_owner(
            factory,
            username="owner",
            display_name="Owner",
            password_reader=_reader(INITIAL_PASSWORD, "different password phrase"),
        )
    assert (await db_session.scalars(select(User))).all() == []

    await admin.bootstrap_owner(
        factory,
        username="owner",
        display_name="Owner",
        password_reader=_reader(INITIAL_PASSWORD, INITIAL_PASSWORD),
    )
    with pytest.raises(admin.LocalAdminError):
        await admin.bootstrap_owner(
            factory,
            username="second",
            display_name="Second",
            password_reader=_reader(REPLACEMENT_PASSWORD, REPLACEMENT_PASSWORD),
        )
    assert len((await db_session.scalars(select(User))).all()) == 1


@pytest.mark.asyncio
async def test_recovery_replaces_hash_and_revokes_every_owner_session(
    db_session: AsyncSession,
) -> None:
    admin = _admin()
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    created = await admin.bootstrap_owner(
        factory,
        username="owner",
        display_name="Owner",
        password_reader=_reader(INITIAL_PASSWORD, INITIAL_PASSWORD),
    )
    now = datetime.now(UTC)
    sessions = [
        AuthSession(
            user_id=created.user_id,
            access_jti=uuid4().hex,
            refresh_token_hash=f"{index:02x}" * 32,
            access_expires_at=now + timedelta(hours=2),
            refresh_expires_at=now + timedelta(days=14),
        )
        for index in range(2)
    ]
    db_session.add_all(sessions)
    await db_session.commit()

    result = await admin.reset_owner_password(
        factory,
        password_reader=_reader(REPLACEMENT_PASSWORD, REPLACEMENT_PASSWORD),
    )

    db_session.expire_all()
    owner = await db_session.get(User, created.user_id)
    assert owner is not None and result.user_id == owner.id
    assert verify_password(owner.password_hash, REPLACEMENT_PASSWORD).valid
    assert not verify_password(owner.password_hash, INITIAL_PASSWORD).valid
    records = (
        await db_session.scalars(select(AuthSession).where(AuthSession.user_id == owner.id))
    ).all()
    assert len(records) == 2 and all(record.revoked_at is not None for record in records)
    event = await db_session.scalar(select(AuditLog).where(AuditLog.action == "auth.owner.reset"))
    assert event is not None and event.outcome == "SUCCESS"
    assert REPLACEMENT_PASSWORD not in f"{result!r}{event.metadata_json!r}"
