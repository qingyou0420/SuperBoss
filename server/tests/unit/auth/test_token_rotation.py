"""Session security behavior exercised against PostgreSQL."""

import asyncio

import jwt
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from superboss.core.config import Settings
from superboss.modules.auth.models import AuthSession
from superboss.modules.auth.repository import AuthRepository
from superboss.modules.auth.service import AuthService, InvalidSession
from superboss.modules.users.models import Role, User
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("role", "ROOT"), ("sub", 1), ("sub", True), ("sub", "bad-uuid"),
        ("session_id", 1), ("session_id", "bad-uuid"), ("jti", 1), ("jti", ""),
        ("iat", "1"), ("iat", 1.5), ("iat", False), ("exp", "1"), ("exp", 1.5), ("exp", False),
    ],
)
async def test_access_claim_anomalies_are_rejected(
    db_session: AsyncSession, active_owner: User, test_settings: Settings, field: str, value: object
) -> None:
    """Invalid exact-six-claim tokens must not authenticate a live session."""
    service = AuthService(db_session, AuthRepository(db_session), UserRepository(db_session), None, test_settings)
    pair = await service.issue_session(active_owner)
    claims = jwt.decode(pair.access_token, test_settings.jwt_secret, algorithms=["HS256"], options={"verify_exp": False})
    claims[field] = value
    forged = jwt.encode(claims, test_settings.jwt_secret, algorithm="HS256")
    with pytest.raises(InvalidSession):
        await service.authenticate_access_token(forged)


@pytest.mark.asyncio
async def test_concurrent_refresh_rotation_has_exactly_one_winner(
    db_session: AsyncSession, active_owner: User, test_settings: Settings, postgres_database: str
) -> None:
    """Removing the row lock permits both concurrent refreshes to rotate one credential."""
    issuer = AuthService(db_session, AuthRepository(db_session), UserRepository(db_session), None, test_settings)
    pair = await issuer.issue_session(active_owner)
    await db_session.commit()
    engine = create_async_engine(postgres_database)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    barrier = asyncio.Barrier(2)

    async def rotate() -> bool:
        async with sessions() as session:
            service = AuthService(session, AuthRepository(session), UserRepository(session), None, test_settings)
            await barrier.wait()
            try:
                await service.rotate_refresh_token(pair.refresh_token)
                await session.commit()
                return True
            except InvalidSession:
                await session.rollback()
                return False

    assert sorted(await asyncio.gather(rotate(), rotate())) == [False, True]
    async with sessions() as session:
        records = (await session.scalars(select(AuthSession))).all()
        assert sum(record.revoked_at is None for record in records) == 1
        assert sum(record.refresh_used_at is not None for record in records) == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_old_token_is_rejected_after_authoritative_role_change(
    db_session: AsyncSession, active_owner: User, test_settings: Settings
) -> None:
    """Removing the DB-role comparison would accept this stale OWNER token as current."""
    service = AuthService(db_session, AuthRepository(db_session), UserRepository(db_session), None, test_settings)
    pair = await service.issue_session(active_owner)
    active_owner.role = Role.STAFF
    await db_session.commit()
    with pytest.raises(InvalidSession):
        await service.authenticate_access_token(pair.access_token)
