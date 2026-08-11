"""Interactive local OWNER bootstrap and password recovery command."""

from __future__ import annotations

import asyncio
import getpass
import sys

from superboss.core.db import async_session_factory
from superboss.modules.auth.admin import (
    LocalAdminError,
    bootstrap_owner,
    build_parser,
    reset_owner_password,
)


async def _run() -> int:
    arguments = build_parser().parse_args()
    factory = async_session_factory()
    if arguments.command == "bootstrap":
        result = await bootstrap_owner(
            factory,
            username=arguments.username,
            display_name=arguments.display_name,
            password_reader=getpass.getpass,
        )
    else:
        result = await reset_owner_password(factory, password_reader=getpass.getpass)
    print(f"Local OWNER updated: {result.user_id}")
    return 0


def main() -> int:
    try:
        return asyncio.run(_run())
    except LocalAdminError as error:
        print(str(error), file=sys.stderr)
        return 2
    except Exception:  # noqa: BLE001 -- database details must not reach the terminal
        print("Local OWNER administration failed.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
