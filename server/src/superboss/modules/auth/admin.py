"""Server-local OWNER bootstrap and password recovery operations."""

from __future__ import annotations

import argparse
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from superboss.modules.audit.models import AuditLog
from superboss.modules.auth.models import AuthSession
from superboss.modules.auth.passwords import PasswordPolicyError, hash_password, validate_password
from superboss.modules.auth.schemas import USERNAME_PATTERN
from superboss.modules.users.models import Role, User, UserStatus

PasswordReader = Callable[[str], str]


class LocalAdminError(Exception):
    """A stable, secret-free failure for local identity administration."""


@dataclass(frozen=True)
class LocalOwnerResult:
    user_id: UUID


def build_parser() -> argparse.ArgumentParser:
    """Build the password-free command-line parser."""
    parser = argparse.ArgumentParser(description="Manage the local SuperBoss OWNER account.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    bootstrap = subparsers.add_parser("bootstrap", help="Create the first local OWNER.")
    bootstrap.add_argument("--username", required=True)
    bootstrap.add_argument("--display-name", required=True)
    subparsers.add_parser("reset", help="Reset the existing OWNER password.")
    return parser


def _read_confirmed_password(password_reader: PasswordReader) -> str:
    password = password_reader("New password: ")
    confirmation = password_reader("Confirm new password: ")
    if password != confirmation:
        raise LocalAdminError("Password confirmation does not match.")
    try:
        validate_password(password)
    except PasswordPolicyError as error:
        raise LocalAdminError("Password does not meet the local policy.") from error
    return password


def _audit(owner: User, action: str) -> AuditLog:
    return AuditLog(
        actor_kind="user",
        actor_id=owner.id,
        action=action,
        object_type="user",
        object_id=owner.id,
        outcome="SUCCESS",
        metadata_json={"actor_role": "OWNER"},
        request_id=uuid4(),
    )


async def bootstrap_owner(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    username: str,
    display_name: str,
    password_reader: PasswordReader,
) -> LocalOwnerResult:
    """Create the sole OWNER and its audit evidence in one transaction."""
    if re.fullmatch(USERNAME_PATTERN, username) is None:
        raise LocalAdminError("Username is invalid.")
    if not display_name or display_name != display_name.strip() or len(display_name) > 255:
        raise LocalAdminError("Display name is invalid.")
    password = _read_confirmed_password(password_reader)
    now = datetime.now(UTC)
    try:
        async with session_factory() as session, session.begin():
            existing = await session.scalar(select(User.id).limit(1).with_for_update())
            if existing is not None:
                raise LocalAdminError("A local identity already exists.")
            owner = User(
                username=username,
                display_name=display_name,
                password_hash=hash_password(password),
                must_change_password=False,
                password_changed_at=now,
                role=Role.OWNER,
                status=UserStatus.ACTIVE,
            )
            session.add(owner)
            await session.flush()
            session.add(_audit(owner, "auth.owner.bootstrap"))
        return LocalOwnerResult(owner.id)
    except IntegrityError as error:
        raise LocalAdminError("A local identity already exists.") from error


async def reset_owner_password(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    password_reader: PasswordReader,
) -> LocalOwnerResult:
    """Replace the OWNER password and revoke every browser session atomically."""
    password = _read_confirmed_password(password_reader)
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        owner = await session.scalar(
            select(User).where(User.role == Role.OWNER).with_for_update()
        )
        if owner is None:
            raise LocalAdminError("The local OWNER does not exist.")
        owner.password_hash = hash_password(password)
        owner.password_changed_at = now
        owner.must_change_password = False
        owner.failed_login_count = 0
        owner.locked_until = None
        await session.execute(
            update(AuthSession)
            .where(AuthSession.user_id == owner.id, AuthSession.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        session.add(_audit(owner, "auth.owner.reset"))
    return LocalOwnerResult(owner.id)
