"""Create the deterministic M1 acceptance identities and projects."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass
from typing import cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from superboss.modules.projects.models import Project, ProjectStatus
from superboss.modules.users.models import Role, User, UserStatus

NORMAL_PROJECT_NAME = "M1 正常项目"
TEST_PROJECT_NAME = "验收测试"


class SeedRefusedError(Exception):
    """A safe, expected refusal with no environment value in its message."""


@dataclass(frozen=True)
class SeedIds:
    owner_id: UUID
    staff_id: UUID
    normal_project_id: UUID
    test_project_id: UUID


def _required_userid(name: str) -> str:
    value = os.getenv(name, "")
    if (
        not value
        or value != value.strip()
        or len(value) > 255
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise SeedRefusedError("Acceptance seed input is invalid.")
    return value


async def _sole_owner(session: AsyncSession) -> User | None:
    return cast(
        User | None,
        await session.scalar(select(User).where(User.role == Role.OWNER).with_for_update()),
    )


async def _acceptance_user(
    session: AsyncSession,
    *,
    userid: str,
    role: Role,
) -> User:
    existing = await session.scalar(
        select(User).where(User.wecom_userid == userid).with_for_update()
    )
    if existing is not None:
        if existing.role != role or (role == Role.STAFF and existing.status != UserStatus.ACTIVE):
            raise SeedRefusedError("Acceptance seed conflicts with an existing identity.")
        return existing
    user = User(
        wecom_userid=userid,
        display_name="",
        role=role,
        status=UserStatus.ACTIVE,
    )
    session.add(user)
    await session.flush()
    return user


async def _acceptance_project(
    session: AsyncSession,
    *,
    name: str,
    is_test: bool,
) -> Project:
    existing = await session.scalar(
        select(Project).where(func.lower(Project.name) == name.lower()).with_for_update()
    )
    if existing is not None:
        if (
            existing.name != name
            or existing.is_test is not is_test
            or existing.status != ProjectStatus.ACTIVE
        ):
            raise SeedRefusedError("Acceptance seed conflicts with an existing project.")
        return existing
    project = Project(name=name, is_test=is_test, status=ProjectStatus.ACTIVE)
    session.add(project)
    await session.flush()
    return project


async def seed(database_url: str, owner_userid: str, staff_userid: str) -> SeedIds:
    engine = create_async_engine(database_url, pool_pre_ping=True)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session, session.begin():
            existing_owner = await _sole_owner(session)
            if existing_owner is not None and existing_owner.wecom_userid != owner_userid:
                raise SeedRefusedError("Acceptance seed does not match the protected OWNER.")
            owner = existing_owner or await _acceptance_user(
                session, userid=owner_userid, role=Role.OWNER
            )
            staff = await _acceptance_user(session, userid=staff_userid, role=Role.STAFF)
            normal_project = await _acceptance_project(
                session, name=NORMAL_PROJECT_NAME, is_test=False
            )
            test_project = await _acceptance_project(
                session, name=TEST_PROJECT_NAME, is_test=True
            )
        return SeedIds(owner.id, staff.id, normal_project.id, test_project.id)
    finally:
        await engine.dispose()


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create deterministic M1 acceptance records without passwords."
    )
    parser.add_argument(
        "--confirm-production-seed",
        action="store_true",
        help="Explicitly allow acceptance records in SUPERBOSS_ENVIRONMENT=production.",
    )
    return parser.parse_args()


def main() -> int:
    arguments = _arguments()
    try:
        if (
            os.getenv("SUPERBOSS_ENVIRONMENT", "development") == "production"
            and not arguments.confirm_production_seed
        ):
            raise SeedRefusedError("Production acceptance seed requires explicit confirmation.")
        database_url = os.getenv("SUPERBOSS_DATABASE_URL", "")
        if not database_url:
            raise SeedRefusedError("Acceptance seed database is not configured.")
        owner_userid = _required_userid("SUPERBOSS_OWNER_WECOM_USERID")
        staff_userid = _required_userid("SUPERBOSS_ACCEPTANCE_STAFF_WECOM_USERID")
        if owner_userid == staff_userid:
            raise SeedRefusedError("Acceptance OWNER and STAFF must be distinct.")
        result = asyncio.run(seed(database_url, owner_userid, staff_userid))
    except SeedRefusedError as error:
        print(str(error), file=sys.stderr)
        return 2
    except Exception:  # noqa: BLE001 -- database/provider details must never reach stderr
        print("Acceptance seed failed.", file=sys.stderr)
        return 1

    print(
        json.dumps(
            {
                "normal_project_id": str(result.normal_project_id),
                "owner_id": str(result.owner_id),
                "staff_id": str(result.staff_id),
                "test_project_id": str(result.test_project_id),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
