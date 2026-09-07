"""Create deterministic local identities and projects for M1 acceptance."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from superboss.modules.agent.models import (
    AgentCard,
    AgentConversation,
    AgentMessage,
    CardKind,
    CardStatus,
    MessageRole,
)
from superboss.modules.agent.service import receipt_line
from superboss.modules.auth.passwords import (
    PasswordPolicyError,
    hash_password,
    validate_password,
)
from superboss.modules.auth.schemas import USERNAME_PATTERN
from superboss.modules.projects.models import Project, ProjectStatus
from superboss.modules.users.models import Role, User, UserStatus

NORMAL_PROJECT_NAME = "M1 正常项目"
TEST_PROJECT_NAME = "验收测试"
VISUAL_CHAT_TITLE = "房租确认"
OWNER_DISPLAY_NAME = "验收老板"
STAFF_DISPLAY_NAME = "验收员工"

PasswordReader = Callable[[str], str]


class SeedRefusedError(Exception):
    """A safe, expected refusal with no input value in its message."""


@dataclass(frozen=True)
class SeedIds:
    owner_id: UUID
    staff_id: UUID
    normal_project_id: UUID
    test_project_id: UUID


def _required_username(name: str) -> str:
    value = os.getenv(name, "")
    if re.fullmatch(USERNAME_PATTERN, value) is None:
        raise SeedRefusedError("Acceptance seed input is invalid.")
    return value


def _validate_seed_password(password: str) -> str:
    try:
        validate_password(password)
    except PasswordPolicyError as error:
        raise SeedRefusedError("Password does not meet the local policy.") from error
    return password


async def _read_passwords(password_reader: PasswordReader) -> tuple[str, str]:
    owner_env = os.getenv("SUPERBOSS_OWNER_PASSWORD", "")
    staff_env = os.getenv("SUPERBOSS_ACCEPTANCE_STAFF_PASSWORD", "")
    if owner_env or staff_env:
        if not owner_env or not staff_env:
            raise SeedRefusedError("Acceptance seed passwords must both come from the environment.")
        return _validate_seed_password(owner_env), _validate_seed_password(staff_env)
    values: list[str] = []
    for label in ("OWNER", "STAFF"):
        password = await asyncio.to_thread(password_reader, f"{label} password: ")
        confirmation = await asyncio.to_thread(password_reader, f"Confirm {label} password: ")
        if password != confirmation:
            raise SeedRefusedError("Password confirmation does not match.")
        values.append(_validate_seed_password(password))
    return values[0], values[1]


async def _sole_owner(session: AsyncSession) -> User | None:
    return cast(
        User | None,
        await session.scalar(select(User).where(User.role == Role.OWNER).with_for_update()),
    )


async def _acceptance_user(
    session: AsyncSession,
    *,
    username: str,
    password: str,
    role: Role,
    display_name: str,
) -> User:
    existing = await session.scalar(select(User).where(User.username == username).with_for_update())
    if existing is not None:
        if existing.role != role or (role == Role.STAFF and existing.status != UserStatus.ACTIVE):
            raise SeedRefusedError("Acceptance seed conflicts with an existing identity.")
        if not existing.display_name:
            existing.display_name = display_name
        return existing
    now = datetime.now(UTC)
    user = User(
        username=username,
        display_name=display_name,
        password_hash=hash_password(password),
        must_change_password=False,
        password_changed_at=now,
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
) -> Project:
    existing = await session.scalar(
        select(Project).where(func.lower(Project.name) == name.lower()).with_for_update()
    )
    if existing is not None:
        if existing.name != name or existing.status != ProjectStatus.ACTIVE:
            raise SeedRefusedError("Acceptance seed conflicts with an existing project.")
        return existing
    project = Project(name=name, description="", status=ProjectStatus.ACTIVE)
    session.add(project)
    await session.flush()
    return project


async def _visual_chat_thread(session: AsyncSession, owner_id: UUID) -> None:
    existing = await session.scalar(
        select(AgentConversation).where(
            AgentConversation.owner_id == owner_id,
            AgentConversation.title == VISUAL_CHAT_TITLE,
        )
    )
    if existing is not None:
        for card in (
            await session.scalars(select(AgentCard).where(AgentCard.conversation_id == existing.id))
        ).all():
            await session.delete(card)
        for message in (
            await session.scalars(
                select(AgentMessage).where(AgentMessage.conversation_id == existing.id)
            )
        ).all():
            await session.delete(message)
        await session.delete(existing)
        await session.flush()
    conversation = AgentConversation(owner_id=owner_id, title=VISUAL_CHAT_TITLE)
    session.add(conversation)
    await session.flush()
    payload = {
        "kind": "COST",
        "scope": "COMPANY",
        "amount_cents": 800000,
        "occurred_on": "2026-09-01",
        "category": "房租",
    }
    user = AgentMessage(
        conversation_id=conversation.id,
        role=MessageRole.USER,
        content="这个月房租 8000",
    )
    session.add(user)
    await session.flush()
    proposed_message = AgentMessage(
        conversation_id=conversation.id,
        role=MessageRole.ASSISTANT,
        content="请确认这张房租卡片。",
    )
    session.add(proposed_message)
    await session.flush()
    proposed = AgentCard(
        conversation_id=conversation.id,
        message_id=proposed_message.id,
        kind=CardKind.FINANCE_ENTRY,
        payload=payload,
        status=CardStatus.PROPOSED,
    )
    session.add(proposed)
    await session.flush()
    proposed_message.card_ids = [proposed.id]
    committed_message = AgentMessage(
        conversation_id=conversation.id,
        role=MessageRole.ASSISTANT,
        content="房租已按你的确认入库。",
    )
    session.add(committed_message)
    await session.flush()
    committed = AgentCard(
        conversation_id=conversation.id,
        message_id=committed_message.id,
        kind=CardKind.FINANCE_ENTRY,
        payload=payload,
        status=CardStatus.COMMITTED,
        decided_at=datetime.now(UTC),
    )
    session.add(committed)
    await session.flush()
    committed_message.card_ids = [committed.id]
    session.add(
        AgentMessage(
            conversation_id=conversation.id,
            role=MessageRole.SYSTEM,
            content=receipt_line(CardKind.FINANCE_ENTRY.value, payload),
        )
    )


async def seed(
    database_url: str,
    owner_username: str,
    owner_password: str,
    staff_username: str,
    staff_password: str,
) -> SeedIds:
    """Create acceptance identities and projects in one transaction."""
    engine = create_async_engine(database_url, pool_pre_ping=True)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session, session.begin():
            existing_owner = await _sole_owner(session)
            if existing_owner is not None and existing_owner.username != owner_username:
                raise SeedRefusedError("Acceptance seed does not match the protected OWNER.")
            owner = existing_owner or await _acceptance_user(
                session,
                username=owner_username,
                password=owner_password,
                role=Role.OWNER,
                display_name=OWNER_DISPLAY_NAME,
            )
            if not owner.display_name:
                owner.display_name = OWNER_DISPLAY_NAME
            staff = await _acceptance_user(
                session,
                username=staff_username,
                password=staff_password,
                role=Role.STAFF,
                display_name=STAFF_DISPLAY_NAME,
            )
            normal_project = await _acceptance_project(session, name=NORMAL_PROJECT_NAME)
            test_project = await _acceptance_project(session, name=TEST_PROJECT_NAME)
            await _visual_chat_thread(session, owner.id)
        return SeedIds(owner.id, staff.id, normal_project.id, test_project.id)
    finally:
        await engine.dispose()


def build_parser() -> argparse.ArgumentParser:
    """Build the secret-free command-line parser."""
    parser = argparse.ArgumentParser(
        description="Create deterministic local M1 acceptance records."
    )
    parser.add_argument(
        "--confirm-production-seed",
        action="store_true",
        help="Explicitly allow acceptance records in production.",
    )
    return parser


async def run_from_environment(
    confirm_production_seed: bool,
    password_reader: PasswordReader,
) -> SeedIds:
    """Validate safe environment input, then read passwords interactively."""
    if (
        os.getenv("SUPERBOSS_ENVIRONMENT", "development") == "production"
        and not confirm_production_seed
    ):
        raise SeedRefusedError("Production acceptance seed requires explicit confirmation.")
    database_url = os.getenv("SUPERBOSS_DATABASE_URL", "")
    if not database_url:
        raise SeedRefusedError("Acceptance seed database is not configured.")
    owner_username = _required_username("SUPERBOSS_OWNER_USERNAME")
    staff_username = _required_username("SUPERBOSS_ACCEPTANCE_STAFF_USERNAME")
    if owner_username == staff_username:
        raise SeedRefusedError("Acceptance OWNER and STAFF must be distinct.")
    owner_password, staff_password = await _read_passwords(password_reader)
    return await seed(
        database_url,
        owner_username,
        owner_password,
        staff_username,
        staff_password,
    )


def main() -> int:
    arguments = build_parser().parse_args()
    try:
        result = asyncio.run(
            run_from_environment(arguments.confirm_production_seed, getpass.getpass)
        )
    except SeedRefusedError as error:
        print(str(error), file=sys.stderr)
        return 2
    except Exception:  # noqa: BLE001 -- database details must never reach stderr
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
