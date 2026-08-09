"""Session security behavior exercised against PostgreSQL."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from superboss.core.config import Settings
from superboss.modules.auth.repository import AuthRepository
from superboss.modules.auth.service import AuthService, InvalidSession
from superboss.modules.users.models import User
from superboss.modules.users.repository import UserRepository


@pytest.mark.asyncio
async def test_refresh_token_is_single_use(
    db_session: AsyncSession, active_owner: User, test_settings: Settings
) -> None:
    """Removing refresh-token consumption would permit replay and fail this test."""
    service = AuthService(
        db_session, AuthRepository(db_session), UserRepository(db_session), None, test_settings
    )
    pair = await service.issue_session(active_owner)

    rotated = await service.rotate_refresh_token(pair.refresh_token)

    assert rotated.refresh_token != pair.refresh_token
    with pytest.raises(InvalidSession):
        await service.rotate_refresh_token(pair.refresh_token)


@pytest.mark.asyncio
async def test_logout_revokes_both_access_and_refresh_tokens(
    db_session: AsyncSession, active_owner: User, test_settings: Settings
) -> None:
    """Omitting either revocation lets a logged-out credential remain valid."""
    service = AuthService(
        db_session, AuthRepository(db_session), UserRepository(db_session), None, test_settings
    )
    pair = await service.issue_session(active_owner)

    await service.logout(pair.access_token, pair.refresh_token)

    with pytest.raises(InvalidSession):
        await service.rotate_refresh_token(pair.refresh_token)
    with pytest.raises(InvalidSession):
        await service.authenticate_access_token(pair.access_token)
