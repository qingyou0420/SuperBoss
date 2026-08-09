"""Real PostgreSQL fixtures for integration tests."""

import os
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from testcontainers.community.postgres import PostgresContainer

from superboss.core.config import get_settings

SERVER_ROOT = Path(__file__).resolve().parents[2]


def migrate_database(database_url: str) -> None:
    """Upgrade a PostgreSQL test database to the current migration head."""
    environment = os.environ.copy()
    environment["SUPERBOSS_DATABASE_URL"] = database_url
    get_settings.cache_clear()
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=SERVER_ROOT,
        env=environment,
        check=True,
    )


@pytest.fixture(scope="session")
def postgres_database() -> Iterator[str]:
    """Migrate an explicit PostgreSQL URL or start a disposable container."""
    external_database_url = os.getenv("SUPERBOSS_TEST_DATABASE_URL")
    if external_database_url is not None:
        migrate_database(external_database_url)
        yield external_database_url
        get_settings.cache_clear()
        return

    with PostgresContainer("postgres:17-alpine") as postgres:
        database_url = postgres.get_connection_url().replace("postgresql+psycopg2", "postgresql+asyncpg")
        migrate_database(database_url)
        yield database_url
        get_settings.cache_clear()


@pytest_asyncio.fixture
async def db_session(postgres_database: str) -> AsyncIterator[AsyncSession]:
    """Provide a clean async session against the migrated PostgreSQL container."""
    engine = create_async_engine(postgres_database)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        async with AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        ) as session:
            yield session
        await transaction.rollback()
    await engine.dispose()
