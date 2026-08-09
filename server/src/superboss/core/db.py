"""SQLAlchemy database primitives."""

from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from superboss.core.config import get_settings


class Base(DeclarativeBase):
    """Declarative base shared by all persisted models."""


@lru_cache
def get_async_engine() -> AsyncEngine:
    """Create the application's async engine from environment-backed settings."""
    return create_async_engine(get_settings().database_url, pool_pre_ping=True)


def async_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return sessions that do not expire ORM objects after commits."""
    return async_sessionmaker(get_async_engine(), expire_on_commit=False)


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """Provide one transactional unit-of-work session for a request."""
    async with async_session_factory()() as session:
        yield session
