"""Alembic environment configured for the async PostgreSQL application engine."""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from superboss.core.db import Base
from superboss.modules.audit import models as audit_models
from superboss.modules.auth import models as auth_models
from superboss.modules.devices import models as device_models
from superboss.modules.files import models as file_models
from superboss.modules.projects import models as project_models
from superboss.modules.users import models as user_models

model_modules = (
    audit_models,
    project_models,
    user_models,
    auth_models,
    file_models,
    device_models,
)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_database_url() -> str:
    """Read the migration target from the same environment contract as the app."""
    try:
        return os.environ["SUPERBOSS_DATABASE_URL"]
    except KeyError as error:
        message = "SUPERBOSS_DATABASE_URL must be set before running Alembic"
        raise RuntimeError(message) from error


def run_migrations_offline() -> None:
    context.configure(
        url=get_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable: AsyncEngine = create_async_engine(get_database_url(), poolclass=pool.NullPool)

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
