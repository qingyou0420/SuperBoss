"""python -m superboss.modules.placeholder"""

import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from superboss.core.config import get_settings
from superboss.modules.placeholder.seed import seed_for_owner


async def main() -> None:
    engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        result = await seed_for_owner(session)
        print(result)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
