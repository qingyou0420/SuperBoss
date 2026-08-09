"""Shared real-PostgreSQL test fixtures."""

import os
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from testcontainers.community.postgres import PostgresContainer

from superboss.core.config import Settings
from superboss.modules.audit.models import AuditLog
from superboss.modules.auth.models import AuthSession, OAuthState
from superboss.modules.devices.models import (
    DeviceConnection,
    DevicePairingCode,
    DevicePairingProject,
    DeviceProjectGrant,
    DeviceScopeGrant,
    DeviceSession,
)
from superboss.modules.files.models import (
    File,
    FileLifecycleOutbox,
    FileStorageCleanup,
    FileUploadLifecycle,
    Upload,
)
from superboss.modules.projects.models import Project, ProjectMember
from superboss.modules.users.models import Role, User, UserStatus

SERVER_ROOT = Path(__file__).resolve().parents[1]


def _migrate(database_url: str) -> None:
    environment = os.environ.copy()
    environment["SUPERBOSS_DATABASE_URL"] = database_url
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=SERVER_ROOT,
        env=environment,
        check=True,
    )


@pytest.fixture(scope="session")
def postgres_database() -> Iterator[str]:
    url = os.getenv("SUPERBOSS_TEST_DATABASE_URL")
    if url:
        _migrate(url)
        yield url
        return
    with PostgresContainer("postgres:17-alpine") as postgres:
        url = postgres.get_connection_url().replace("postgresql+psycopg2", "postgresql+asyncpg")
        _migrate(url)
        yield url


@pytest_asyncio.fixture
async def db_session(postgres_database: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(postgres_database)
    async def clear() -> None:
        async with engine.begin() as connection:
            await connection.execute(delete(AuditLog))
            try:
                from superboss.modules.imports.models import ImportAttachment, ImportJob
            except ModuleNotFoundError:
                # Stage-1 RED intentionally runs before Task 10 production modules exist.
                pass
            else:
                await connection.execute(delete(ImportAttachment))
                await connection.execute(delete(ImportJob))
            await connection.execute(delete(DeviceScopeGrant))
            await connection.execute(delete(DeviceProjectGrant))
            await connection.execute(delete(DeviceSession))
            await connection.execute(delete(DeviceConnection))
            await connection.execute(delete(DevicePairingProject))
            await connection.execute(delete(DevicePairingCode))
            await connection.execute(delete(FileLifecycleOutbox))
            await connection.execute(delete(FileStorageCleanup))
            await connection.execute(delete(FileUploadLifecycle))
            await connection.execute(delete(Upload))
            await connection.execute(delete(File))
            await connection.execute(delete(ProjectMember))
            await connection.execute(delete(Project))
            await connection.execute(delete(OAuthState))
            await connection.execute(delete(AuthSession))
            await connection.execute(delete(User))

    await clear()
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session
            await session.rollback()
    finally:
        await clear()
        await engine.dispose()


@pytest.fixture
def test_settings(postgres_database: str) -> Settings:
    return Settings(
        environment="test",
        database_url=postgres_database,
        jwt_secret="test-only-signing-secret-with-at-least-thirty-two-bytes",
        wecom_fake=True,
        owner_wecom_userid="owner-1",
        lifecycle_reconcile_interval_seconds=0,
    )


@pytest_asyncio.fixture
async def active_owner(db_session: AsyncSession) -> User:
    owner = User(
        wecom_userid="owner-1", display_name="Owner", role=Role.OWNER, status=UserStatus.ACTIVE
    )
    db_session.add(owner)
    await db_session.flush()
    return owner
