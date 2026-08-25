"""Exact browser credential lifetime contracts without external services."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from superboss.core import security
from superboss.core.config import Settings
from superboss.core.security import hash_token, issue_access_token, issue_device_access_token
from superboss.modules.auth import service as auth_service_module
from superboss.modules.auth.models import AuthSession
from superboss.modules.auth.service import AuthService
from superboss.modules.users.models import Role
from tests.identity import local_user


def _settings() -> Settings:
    return Settings(jwt_secret="test-only-signing-secret-with-at-least-thirty-two-bytes")


def _claims(token: str, settings: Settings) -> dict[str, object]:
    return jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=["HS256"],
        options={"verify_exp": False, "verify_iat": False},
    )


def test_browser_access_token_expires_exactly_two_hours_after_issuance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Changing browser access issuance back to 15 minutes must fail this contract."""
    issued_at = datetime(2030, 1, 2, 3, 4, 5, 654321, tzinfo=UTC)
    settings = _settings()
    monkeypatch.setattr(security, "utcnow", lambda: issued_at)

    token, returned_expires_at = issue_access_token(
        settings, uuid4(), Role.OWNER.value, uuid4()
    )

    claims = _claims(token, settings)
    expected_expires_at = issued_at.replace(microsecond=0) + timedelta(hours=2)
    assert int(claims["exp"]) - int(claims["iat"]) == 7200
    assert returned_expires_at == expected_expires_at
    assert returned_expires_at == datetime.fromtimestamp(int(claims["exp"]), UTC)


@pytest.mark.asyncio
async def test_issue_session_returns_and_persists_exact_browser_lifetimes(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Short access or 30-day refresh values must not escape the real issue flow."""
    issued_at = datetime.now(UTC).replace(microsecond=654321) - timedelta(minutes=1)
    settings = _settings()
    user = local_user("owner", display_name="Owner", role=Role.OWNER)
    db_session.add(user)
    await db_session.flush()
    monkeypatch.setattr(security, "utcnow", lambda: issued_at)
    monkeypatch.setattr(auth_service_module, "utcnow", lambda: issued_at)

    pair = await AuthService(db_session, settings).issue_session(user)

    claims = _claims(pair.access_token, settings)
    stored = await db_session.scalar(
        select(AuthSession).where(AuthSession.refresh_token_hash == hash_token(pair.refresh_token))
    )
    expected_access_expiry = issued_at.replace(microsecond=0) + timedelta(hours=2)
    expected_refresh_expiry = issued_at + timedelta(days=14)
    assert stored is not None
    assert pair.access_expires_at == expected_access_expiry
    assert datetime.fromtimestamp(int(claims["exp"]), UTC) == expected_access_expiry
    assert stored.access_expires_at == expected_access_expiry
    assert pair.refresh_expires_at == expected_refresh_expiry
    assert stored.refresh_expires_at == expected_refresh_expiry


@pytest.mark.asyncio
async def test_refresh_rotation_reissues_exact_browser_lifetimes(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rotation must start a fresh 2-hour/14-day session from the rotation instant."""
    clock = {"now": datetime.now(UTC).replace(microsecond=654321) - timedelta(minutes=1)}
    settings = _settings()
    user = local_user("owner", display_name="Owner", role=Role.OWNER)
    db_session.add(user)
    await db_session.flush()
    monkeypatch.setattr(security, "utcnow", lambda: clock["now"])
    monkeypatch.setattr(auth_service_module, "utcnow", lambda: clock["now"])
    service = AuthService(db_session, settings)
    initial = await service.issue_session(user)
    initial_record = await db_session.scalar(
        select(AuthSession).where(
            AuthSession.refresh_token_hash == hash_token(initial.refresh_token)
        )
    )
    assert initial_record is not None
    rotation_time = clock["now"] + timedelta(seconds=30)
    clock["now"] = rotation_time

    rotated = await service.rotate_refresh_token(initial.refresh_token)

    claims = _claims(rotated.access_token, settings)
    stored = await db_session.scalar(
        select(AuthSession).where(
            AuthSession.refresh_token_hash == hash_token(rotated.refresh_token)
        )
    )
    assert stored is not None
    assert initial_record.refresh_used_at == rotation_time
    assert initial_record.revoked_at == rotation_time
    expected_access_expiry = rotation_time.replace(microsecond=0) + timedelta(hours=2)
    assert rotated.access_expires_at == expected_access_expiry
    assert datetime.fromtimestamp(int(claims["exp"]), UTC) == rotated.access_expires_at
    assert stored.access_expires_at == rotated.access_expires_at
    assert rotated.refresh_expires_at == rotation_time + timedelta(days=14)
    assert stored.refresh_expires_at == rotated.refresh_expires_at


def test_device_access_token_remains_exactly_two_hours() -> None:
    """Browser lifetime changes must not alter the device access-token contract."""
    issued_at = datetime(2030, 4, 5, 6, 7, 8, tzinfo=UTC)
    settings = _settings()

    token, returned_expires_at = issue_device_access_token(
        settings,
        device_id=uuid4(),
        owner_id=uuid4(),
        session_id=uuid4(),
        access_jti=uuid4(),
        issued_at=issued_at,
    )

    claims = _claims(token, settings)
    assert int(claims["exp"]) - int(claims["iat"]) == 7200
    assert returned_expires_at == issued_at + timedelta(hours=2)
