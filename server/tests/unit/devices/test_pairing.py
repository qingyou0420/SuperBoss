"""Least-privilege device credential lifecycle contracts."""

import asyncio
import importlib
import json
import time
from datetime import UTC, datetime, timedelta
from types import ModuleType
from uuid import uuid4

import asyncpg
import jwt
import pytest
from sqlalchemy import delete, event, func, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from superboss.core.security import hash_token
from superboss.modules.audit.models import AuditLog
from superboss.modules.projects.models import Project, ProjectStatus
from superboss.modules.users.models import Role, UserStatus


def device_contract() -> tuple[ModuleType, ModuleType]:
    """Load the wished-for API lazily so RED failures identify the missing feature."""
    try:
        return (
            importlib.import_module("superboss.modules.devices.models"),
            importlib.import_module("superboss.modules.devices.service"),
        )
    except ModuleNotFoundError:
        pytest.fail("Task 9 devices module is not implemented")


@pytest.fixture
def session_factory(db_session: AsyncSession) -> async_sessionmaker[AsyncSession]:
    assert db_session.bind is not None
    return async_sessionmaker(db_session.bind, expire_on_commit=False)


@pytest.fixture
def reference_time() -> datetime:
    return datetime(2026, 8, 9, 12, 0, tzinfo=UTC)


async def seed_projects(
    db_session: AsyncSession, *names: str, status: ProjectStatus = ProjectStatus.ACTIVE
) -> list[Project]:
    projects = [Project(name=name, status=status) for name in names]
    db_session.add_all(projects)
    await db_session.commit()
    return projects


def service_for(
    session_factory: async_sessionmaker[AsyncSession], test_settings: object, now: datetime
) -> object:
    _models, service = device_contract()
    return service.DeviceService(session_factory, test_settings, clock=lambda: now)


@pytest.mark.asyncio
async def test_pairing_code_is_high_entropy_hash_only_and_snapshots_active_projects(
    db_session: AsyncSession,
    active_owner: object,
    session_factory: async_sessionmaker[AsyncSession],
    test_settings: object,
    reference_time: datetime,
) -> None:
    """Persisting the raw code or live-only selection would leak or widen authorization."""
    models, _service = device_contract()
    projects = await seed_projects(db_session, "Device target A", "Device target B")
    service = service_for(session_factory, test_settings, reference_time)
    request_id = uuid4()

    issued = await service.create_pairing_code(
        active_owner.id, [projects[0].id, projects[1].id], request_id=request_id
    )

    assert len(issued.raw_code) >= 64
    assert issued.expires_at == reference_time + timedelta(minutes=10)
    async with session_factory() as session:
        row = await session.scalar(select(models.DevicePairingCode))
        assert row is not None
        assert row.code_hash == hash_token(issued.raw_code)
        snapshots = set(
            await session.scalars(
                select(models.DevicePairingProject.project_id).where(
                    models.DevicePairingProject.pairing_code_id == row.id
                )
            )
        )
        events = list(await session.scalars(select(AuditLog)))
    assert snapshots == {project.id for project in projects}
    persisted = json.dumps(
        {
            "code_hash": row.code_hash,
            "events": [event.metadata_json for event in events],
        }
    )
    assert issued.raw_code not in persisted
    assert len(events) == 1
    assert events[0].action == "device.pairing_code.create"
    assert events[0].actor_id == active_owner.id and events[0].outcome == "SUCCESS"


@pytest.mark.asyncio
@pytest.mark.parametrize("project_case", ["none", "duplicate", "missing", "archived"])
async def test_pairing_code_rejects_invalid_project_selection_without_side_effects(
    db_session: AsyncSession,
    active_owner: object,
    session_factory: async_sessionmaker[AsyncSession],
    test_settings: object,
    reference_time: datetime,
    project_case: str,
) -> None:
    """Client project IDs must not bypass unique, existing, ACTIVE server state."""
    models, service_module = device_contract()
    active = (await seed_projects(db_session, f"Selection {project_case}"))[0]
    archived = (
        await seed_projects(
            db_session, f"Selection archived {project_case}", status=ProjectStatus.ARCHIVED
        )
    )[0]
    selections = {
        "none": [],
        "duplicate": [active.id, active.id],
        "missing": [uuid4()],
        "archived": [archived.id],
    }
    service = service_for(session_factory, test_settings, reference_time)

    with pytest.raises(service_module.InvalidDeviceGrant):
        await service.create_pairing_code(
            active_owner.id, selections[project_case], request_id=uuid4()
        )

    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(models.DevicePairingCode)) == 0
        assert await session.scalar(select(func.count()).select_from(AuditLog)) == 0


@pytest.mark.asyncio
async def test_pair_consumes_code_once_and_issues_exact_scoped_credentials(
    db_session: AsyncSession,
    active_owner: object,
    session_factory: async_sessionmaker[AsyncSession],
    test_settings: object,
    reference_time: datetime,
) -> None:
    """A replay or OWNER-shaped token would let the connector exceed its grant."""
    models, service_module = device_contract()
    project = (await seed_projects(db_session, "Pair once"))[0]
    service = service_for(session_factory, test_settings, reference_time)
    issued = await service.create_pairing_code(
        active_owner.id, [project.id], request_id=uuid4()
    )

    pair = await service.pair(issued.raw_code, "  Owner-PC  ", request_id=uuid4())

    claims = jwt.decode(
        pair.access_token,
        test_settings.jwt_secret,
        algorithms=["HS256"],
        options={"verify_exp": False},
    )
    assert set(claims) == {
        "sub",
        "device_id",
        "owner_id",
        "scopes",
        "session_id",
        "iat",
        "exp",
        "jti",
    }
    assert claims["sub"] == claims["device_id"] == str(pair.device_id)
    assert claims["owner_id"] == str(active_owner.id)
    assert claims["scopes"] == [
        "imports:create",
        "imports:read-own",
        "imports:submit",
        "imports:upload",
    ]
    assert claims["exp"] - claims["iat"] == 2 * 60 * 60
    assert pair.refresh_expires_at == reference_time + timedelta(days=14)
    assert pair.token_type == "Bearer"
    async with session_factory() as session:
        device = await session.get(models.DeviceConnection, pair.device_id)
        pairing = await session.scalar(select(models.DevicePairingCode))
        refresh = await session.scalar(select(models.DeviceSession))
        grants = set(await session.scalars(select(models.DeviceProjectGrant.project_id)))
        scope_grants = set(await session.scalars(select(models.DeviceScopeGrant.scope)))
        events = list(await session.scalars(select(AuditLog).order_by(AuditLog.created_at)))
    assert device is not None and device.name == "Owner-PC"
    assert pairing is not None and pairing.used_at == reference_time
    assert refresh is not None and refresh.refresh_token_hash == hash_token(pair.refresh_token)
    assert grants == {project.id}
    assert scope_grants == {
        "imports:create",
        "imports:read-own",
        "imports:submit",
        "imports:upload",
    }
    assert pair.refresh_token not in json.dumps([event.metadata_json for event in events])
    assert [event.action for event in events] == ["device.pairing_code.create", "device.pair"]
    with pytest.raises(service_module.InvalidDeviceCredential):
        await service.pair(issued.raw_code, "Second-PC", request_id=uuid4())


@pytest.mark.asyncio
async def test_pairing_expiry_boundary_is_exclusive(
    db_session: AsyncSession,
    active_owner: object,
    session_factory: async_sessionmaker[AsyncSession],
    test_settings: object,
    reference_time: datetime,
) -> None:
    """Accepting a code at exactly expires_at would exceed the ten-minute lifetime."""
    _models, service_module = device_contract()
    project = (await seed_projects(db_session, "Expiry boundary"))[0]
    issue_service = service_for(session_factory, test_settings, reference_time)
    issued = await issue_service.create_pairing_code(
        active_owner.id, [project.id], request_id=uuid4()
    )
    expired_service = service_for(
        session_factory, test_settings, reference_time + timedelta(minutes=10)
    )

    with pytest.raises(service_module.InvalidDeviceCredential):
        await expired_service.pair(issued.raw_code, "Owner-PC", request_id=uuid4())


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["invalid", "replayed", "expired"])
async def test_pair_denial_audit_uses_one_safe_reason_without_credential_material(
    db_session: AsyncSession,
    active_owner: object,
    session_factory: async_sessionmaker[AsyncSession],
    test_settings: object,
    reference_time: datetime,
    case: str,
) -> None:
    """Pair failure evidence must not become a credential oracle or secret sink."""
    _models, service_module = device_contract()
    project = (await seed_projects(db_session, f"Pair denied {case}"))[0]
    service = service_for(session_factory, test_settings, reference_time)
    issued = await service.create_pairing_code(
        active_owner.id, [project.id], request_id=uuid4()
    )
    if case == "invalid":
        raw_code = "not-a-valid-code"
    elif case == "replayed":
        await service.pair(issued.raw_code, "First-PC", request_id=uuid4())
        raw_code = issued.raw_code
    else:
        service = service_for(
            session_factory, test_settings, reference_time + timedelta(minutes=10)
        )
        raw_code = issued.raw_code

    with pytest.raises(service_module.InvalidDeviceCredential):
        await service.pair(raw_code, "Denied-PC", request_id=uuid4())

    async with session_factory() as session:
        denied = list(
            await session.scalars(
                select(AuditLog).where(
                    AuditLog.action == "device.pair", AuditLog.outcome == "DENIED"
                )
            )
        )
    assert len(denied) == 1
    assert denied[0].actor_kind == "system" and denied[0].actor_id is None
    assert denied[0].metadata_json == {
        "actor_role": None,
        "reason": "INVALID_CREDENTIAL",
    }
    serialized = json.dumps(denied[0].metadata_json)
    assert raw_code not in serialized and hash_token(raw_code) not in serialized


@pytest.mark.asyncio
async def test_concurrent_pairing_code_consumption_has_exactly_one_winner(
    db_session: AsyncSession,
    active_owner: object,
    session_factory: async_sessionmaker[AsyncSession],
    test_settings: object,
    reference_time: datetime,
) -> None:
    """A non-locking read-then-write would create two devices from one code."""
    models, service_module = device_contract()
    project = (await seed_projects(db_session, "Pair race"))[0]
    service = service_for(session_factory, test_settings, reference_time)
    issued = await service.create_pairing_code(
        active_owner.id, [project.id], request_id=uuid4()
    )

    results = await asyncio.gather(
        service.pair(issued.raw_code, "Race-A", request_id=uuid4()),
        service.pair(issued.raw_code, "Race-B", request_id=uuid4()),
        return_exceptions=True,
    )

    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert sum(isinstance(result, service_module.InvalidDeviceCredential) for result in results) == 1
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(models.DeviceConnection)) == 1
        assert await session.scalar(select(func.count()).select_from(models.DeviceSession)) == 1
        assert await session.scalar(select(func.count()).select_from(models.DeviceProjectGrant)) == 1


async def paired_fixture(
    db_session: AsyncSession,
    active_owner: object,
    session_factory: async_sessionmaker[AsyncSession],
    test_settings: object,
    now: datetime,
    name: str,
) -> tuple[object, object, Project]:
    project = (await seed_projects(db_session, name))[0]
    service = service_for(session_factory, test_settings, now)
    code = await service.create_pairing_code(active_owner.id, [project.id], request_id=uuid4())
    pair = await service.pair(code.raw_code, "Owner-PC", request_id=uuid4())
    return service, pair, project


@pytest.mark.asyncio
async def test_refresh_rotates_once_and_invalidates_the_old_access_jti(
    db_session: AsyncSession,
    active_owner: object,
    session_factory: async_sessionmaker[AsyncSession],
    test_settings: object,
    reference_time: datetime,
) -> None:
    """Keeping the old JTI or refresh credential valid would defeat rotation."""
    _models, service_module = device_contract()
    service, first, _project = await paired_fixture(
        db_session,
        active_owner,
        session_factory,
        test_settings,
        reference_time,
        "Refresh once",
    )

    rotated = await service.refresh(first.refresh_token, request_id=uuid4())

    assert rotated.refresh_token != first.refresh_token
    assert rotated.access_token != first.access_token
    with pytest.raises(service_module.InvalidDeviceCredential):
        await service.authenticate_access_token(first.access_token)
    actor = await service.authenticate_access_token(rotated.access_token)
    assert actor.kind == "device" and actor.role is None and actor.subject_id == first.device_id
    with pytest.raises(service_module.InvalidDeviceCredential):
        await service.refresh(first.refresh_token, request_id=uuid4())


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["invalid", "reused", "expired"])
async def test_refresh_denial_audit_uses_one_safe_reason_without_credential_material(
    db_session: AsyncSession,
    active_owner: object,
    session_factory: async_sessionmaker[AsyncSession],
    test_settings: object,
    reference_time: datetime,
    case: str,
) -> None:
    """Refresh errors must share one safe outcome and never persist supplied credentials."""
    _models, service_module = device_contract()
    service, pair, _project = await paired_fixture(
        db_session,
        active_owner,
        session_factory,
        test_settings,
        reference_time,
        f"Refresh denied {case}",
    )
    if case == "invalid":
        raw_refresh = "not-a-valid-refresh"
    elif case == "reused":
        await service.refresh(pair.refresh_token, request_id=uuid4())
        raw_refresh = pair.refresh_token
    else:
        service = service_for(
            session_factory, test_settings, reference_time + timedelta(days=14)
        )
        raw_refresh = pair.refresh_token

    with pytest.raises(service_module.InvalidDeviceCredential):
        await service.refresh(raw_refresh, request_id=uuid4())

    async with session_factory() as session:
        denied = list(
            await session.scalars(
                select(AuditLog).where(
                    AuditLog.action == "device.refresh", AuditLog.outcome == "DENIED"
                )
            )
        )
    assert len(denied) == 1
    assert denied[0].metadata_json == {
        "actor_role": None,
        "reason": "INVALID_CREDENTIAL",
    }
    serialized = json.dumps(denied[0].metadata_json)
    assert raw_refresh not in serialized and hash_token(raw_refresh) not in serialized


@pytest.mark.asyncio
async def test_concurrent_refresh_has_exactly_one_winner(
    db_session: AsyncSession,
    active_owner: object,
    session_factory: async_sessionmaker[AsyncSession],
    test_settings: object,
    reference_time: datetime,
) -> None:
    """Concurrent reuse must not mint multiple successor sessions."""
    models, service_module = device_contract()
    service, first, _project = await paired_fixture(
        db_session,
        active_owner,
        session_factory,
        test_settings,
        reference_time,
        "Refresh race",
    )

    results = await asyncio.gather(
        service.refresh(first.refresh_token, request_id=uuid4()),
        service.refresh(first.refresh_token, request_id=uuid4()),
        return_exceptions=True,
    )

    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert sum(isinstance(result, service_module.InvalidDeviceCredential) for result in results) == 1
    async with session_factory() as session:
        sessions = list(await session.scalars(select(models.DeviceSession)))
    assert len(sessions) == 2
    assert sum(item.revoked_at is None for item in sessions) == 1


@pytest.mark.asyncio
async def test_live_owner_device_and_project_grants_control_access_immediately(
    db_session: AsyncSession,
    active_owner: object,
    session_factory: async_sessionmaker[AsyncSession],
    test_settings: object,
    reference_time: datetime,
) -> None:
    """Trusting JWT role/projects would retain revoked or removed authority."""
    models, _service_module = device_contract()
    service, pair, first_project = await paired_fixture(
        db_session,
        active_owner,
        session_factory,
        test_settings,
        reference_time,
        "Live grant A",
    )
    second_project = (await seed_projects(db_session, "Live grant B"))[0]

    async with session_factory() as session, session.begin():
        await session.execute(
            delete(models.DeviceProjectGrant).where(
                models.DeviceProjectGrant.device_id == pair.device_id,
                models.DeviceProjectGrant.project_id == first_project.id,
            )
        )
        session.add(
            models.DeviceProjectGrant(device_id=pair.device_id, project_id=second_project.id)
        )
    actor = await service.authenticate_access_token(pair.access_token)
    assert actor.project_ids == frozenset({second_project.id})

    async with session_factory() as session, session.begin():
        project = await session.get(Project, second_project.id)
        assert project is not None
        project.status = ProjectStatus.ARCHIVED
    actor = await service.authenticate_access_token(pair.access_token)
    assert actor.project_ids == frozenset()


@pytest.mark.asyncio
@pytest.mark.parametrize("owner_change", ["disabled", "demoted"])
async def test_live_owner_status_and_role_are_required_for_device_access(
    db_session: AsyncSession,
    active_owner: object,
    session_factory: async_sessionmaker[AsyncSession],
    test_settings: object,
    reference_time: datetime,
    owner_change: str,
) -> None:
    """An old device JWT cannot preserve authority after OWNER state changes."""
    _models, service_module = device_contract()
    service, pair, _project = await paired_fixture(
        db_session,
        active_owner,
        session_factory,
        test_settings,
        reference_time,
        f"Owner state {owner_change}",
    )
    async with session_factory() as session, session.begin():
        owner = await session.get(type(active_owner), active_owner.id)
        assert owner is not None
        if owner_change == "disabled":
            owner.status = UserStatus.DISABLED
        else:
            owner.role = Role.STAFF
    with pytest.raises(service_module.InvalidDeviceCredential):
        await service.authenticate_access_token(pair.access_token)


@pytest.mark.asyncio
async def test_successful_access_updates_last_used(
    db_session: AsyncSession,
    active_owner: object,
    session_factory: async_sessionmaker[AsyncSession],
    test_settings: object,
    reference_time: datetime,
) -> None:
    """A successful device use must leave current activity evidence without an audit row."""
    models, _service = device_contract()
    _service, pair, _project = await paired_fixture(
        db_session,
        active_owner,
        session_factory,
        test_settings,
        reference_time,
        "Device last used",
    )
    used_at = reference_time + timedelta(minutes=1)
    service = service_for(session_factory, test_settings, used_at)

    actor = await service.authenticate_access_token(pair.access_token)

    async with session_factory() as session:
        device = await session.get(models.DeviceConnection, pair.device_id)
    assert actor.kind == "device" and actor.role is None
    assert device is not None and device.last_used_at == used_at


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "extra_role",
        "missing_claim",
        "sub_mismatch",
        "unknown_scope",
        "session",
        "jti",
        "expired",
        "session_times",
    ],
)
async def test_device_access_claims_are_exact_and_fail_with_one_safe_error(
    db_session: AsyncSession,
    active_owner: object,
    session_factory: async_sessionmaker[AsyncSession],
    test_settings: object,
    reference_time: datetime,
    case: str,
) -> None:
    """Malformed or browser-shaped claims must never cross the device actor boundary."""
    _models, service_module = device_contract()
    service, pair, _project = await paired_fixture(
        db_session,
        active_owner,
        session_factory,
        test_settings,
        reference_time,
        f"Claims {case}",
    )
    claims = jwt.decode(
        pair.access_token,
        test_settings.jwt_secret,
        algorithms=["HS256"],
        options={"verify_exp": False},
    )
    if case == "extra_role":
        claims["role"] = "OWNER"
    elif case == "missing_claim":
        del claims["owner_id"]
    elif case == "sub_mismatch":
        claims["sub"] = str(uuid4())
    elif case == "unknown_scope":
        claims["scopes"] = [*claims["scopes"], "projects:read"]
    elif case == "session":
        claims["session_id"] = str(uuid4())
    elif case == "jti":
        claims["jti"] = str(uuid4())
    elif case == "expired":
        claims["exp"] = claims["iat"] - 1
    else:
        claims["iat"] -= 60
        claims["exp"] -= 60
    token = jwt.encode(claims, test_settings.jwt_secret, algorithm="HS256")

    with pytest.raises(service_module.InvalidDeviceCredential, match="Device credential is invalid"):
        await service.authenticate_access_token(token)


async def _wait_for_database_lock(database_url: str, application_name: str) -> None:
    connection = await asyncpg.connect(
        database_url.replace("postgresql+asyncpg", "postgresql")
    )
    deadline = time.monotonic() + 5
    try:
        while time.monotonic() < deadline:
            waiting = await connection.fetchval(
                "SELECT EXISTS ("
                "SELECT 1 FROM pg_stat_activity "
                "WHERE application_name = $1 AND state = 'active' "
                "AND wait_event_type = 'Lock')",
                application_name,
            )
            if waiting:
                return
            await asyncio.sleep(0.02)
    finally:
        await connection.close()
    raise AssertionError(f"{application_name} did not reach the PostgreSQL lock barrier")


def _isolated_factory(
    database_url: str, application_name: str
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        database_url,
        connect_args={"server_settings": {"application_name": application_name}},
    )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["refresh", "authenticate"])
async def test_revoke_and_credential_use_share_one_deadlock_free_lock_order(
    db_session: AsyncSession,
    active_owner: object,
    session_factory: async_sessionmaker[AsyncSession],
    test_settings: object,
    reference_time: datetime,
    postgres_database: str,
    operation: str,
) -> None:
    """Session-first use and device-first revoke can deadlock and leave authority alive."""
    models, service_module = device_contract()
    setup_service, pair, _project = await paired_fixture(
        db_session,
        active_owner,
        session_factory,
        test_settings,
        reference_time,
        f"Lock order {operation}",
    )
    del setup_service
    lock_connection = await asyncpg.connect(
        postgres_database.replace("postgresql+asyncpg", "postgresql")
    )
    lock_transaction = lock_connection.transaction()
    await lock_transaction.start()
    lock_released = False
    await lock_connection.execute(
        "SELECT id FROM sessions WHERE refresh_token_hash = $1 FOR UPDATE",
        hash_token(pair.refresh_token),
    )
    use_name = f"task9_{operation}_{uuid4().hex}"
    revoke_name = f"task9_revoke_{uuid4().hex}"
    use_engine, use_factory = _isolated_factory(postgres_database, use_name)
    revoke_engine, revoke_factory = _isolated_factory(postgres_database, revoke_name)
    use_service = service_for(use_factory, test_settings, reference_time)
    revoke_service = service_for(revoke_factory, test_settings, reference_time)
    use_task: asyncio.Task[object] | None = None
    revoke_task: asyncio.Task[object] | None = None
    try:
        if operation == "refresh":
            use_task = asyncio.create_task(
                use_service.refresh(pair.refresh_token, request_id=uuid4())
            )
        else:
            use_task = asyncio.create_task(
                use_service.authenticate_access_token(pair.access_token)
            )
        await _wait_for_database_lock(postgres_database, use_name)
        revoke_task = asyncio.create_task(
            revoke_service.revoke(active_owner.id, pair.device_id, request_id=uuid4())
        )
        await _wait_for_database_lock(postgres_database, revoke_name)
        await lock_transaction.rollback()
        lock_released = True
        results = await asyncio.wait_for(
            asyncio.gather(use_task, revoke_task, return_exceptions=True), timeout=10
        )
        assert not [result for result in results if isinstance(result, Exception)]

        verifier = service_for(session_factory, test_settings, reference_time)
        with pytest.raises(service_module.InvalidDeviceCredential):
            await verifier.authenticate_access_token(pair.access_token)
        if operation == "refresh":
            rotated = results[0]
            with pytest.raises(service_module.InvalidDeviceCredential):
                await verifier.authenticate_access_token(rotated.access_token)
            with pytest.raises(service_module.InvalidDeviceCredential):
                await verifier.refresh(rotated.refresh_token, request_id=uuid4())

        async with session_factory() as session:
            device = await session.get(models.DeviceConnection, pair.device_id)
            sessions = list(
                await session.scalars(
                    select(models.DeviceSession).where(
                        models.DeviceSession.device_id == pair.device_id
                    )
                )
            )
            audits = list(
                await session.scalars(
                    select(AuditLog).where(AuditLog.object_id == pair.device_id)
                )
            )
        assert device is not None and device.revoked_at == reference_time
        assert device.last_used_at is None or device.last_used_at <= device.revoked_at
        assert sessions and all(item.revoked_at == reference_time for item in sessions)
        assert len([event for event in audits if event.action == "device.revoke"]) == 1
        assert all(event.action != "device.use" for event in audits)
    finally:
        if not lock_released:
            await lock_transaction.rollback()
        await lock_connection.close()
        for task in (use_task, revoke_task):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in (use_task, revoke_task) if task is not None),
            return_exceptions=True,
        )
        await use_engine.dispose()
        await revoke_engine.dispose()


@pytest.mark.asyncio
async def test_revoke_is_idempotent_and_immediately_rejects_access_and_refresh(
    db_session: AsyncSession,
    active_owner: object,
    session_factory: async_sessionmaker[AsyncSession],
    test_settings: object,
    reference_time: datetime,
) -> None:
    """Revoking only UI state or one credential would leave usable device authority."""
    models, service_module = device_contract()
    service, pair, _project = await paired_fixture(
        db_session,
        active_owner,
        session_factory,
        test_settings,
        reference_time,
        "Revoke device",
    )

    await service.revoke(active_owner.id, pair.device_id, request_id=uuid4())
    await service.revoke(active_owner.id, pair.device_id, request_id=uuid4())

    with pytest.raises(service_module.InvalidDeviceCredential):
        await service.authenticate_access_token(pair.access_token)
    with pytest.raises(service_module.InvalidDeviceCredential):
        await service.refresh(pair.refresh_token, request_id=uuid4())
    async with session_factory() as session:
        device = await session.get(models.DeviceConnection, pair.device_id)
        sessions = list(
            await session.scalars(
                select(models.DeviceSession).where(models.DeviceSession.device_id == pair.device_id)
            )
        )
    assert device is not None and device.revoked_at == reference_time
    assert sessions and all(item.revoked_at == reference_time for item in sessions)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["pair", "refresh", "revoke"])
async def test_audit_failure_rolls_back_device_mutations(
    db_session: AsyncSession,
    active_owner: object,
    session_factory: async_sessionmaker[AsyncSession],
    test_settings: object,
    reference_time: datetime,
    operation: str,
) -> None:
    """Audit persistence must share the business transaction for sensitive mutations."""
    models, _service = device_contract()
    service, pair, project = await paired_fixture(
        db_session,
        active_owner,
        session_factory,
        test_settings,
        reference_time,
        f"Audit rollback {operation}",
    )
    second_code = await service.create_pairing_code(
        active_owner.id, [project.id], request_id=uuid4()
    )

    def fail_target(_mapper: object, _connection: object, target: AuditLog) -> None:
        if target.action == f"device.{operation}":
            raise RuntimeError("audit unavailable")

    event.listen(AuditLog, "before_insert", fail_target)
    try:
        with pytest.raises(RuntimeError, match="audit unavailable"):
            if operation == "pair":
                await service.pair(second_code.raw_code, "Second-PC", request_id=uuid4())
            elif operation == "refresh":
                await service.refresh(pair.refresh_token, request_id=uuid4())
            else:
                await service.revoke(active_owner.id, pair.device_id, request_id=uuid4())
    finally:
        event.remove(AuditLog, "before_insert", fail_target)

    async with session_factory() as session:
        devices = list(await session.scalars(select(models.DeviceConnection)))
        pairing = await session.scalar(
            select(models.DevicePairingCode).where(
                models.DevicePairingCode.code_hash == hash_token(second_code.raw_code)
            )
        )
        original_device = await session.get(models.DeviceConnection, pair.device_id)
        current = await session.scalar(
            select(models.DeviceSession).where(
                models.DeviceSession.refresh_token_hash == hash_token(pair.refresh_token)
            )
        )
    assert pairing is not None and pairing.used_at is None
    assert original_device is not None and original_device.revoked_at is None
    assert current is not None and current.revoked_at is None and current.refresh_used_at is None
    assert len(devices) == 1


@pytest.mark.asyncio
async def test_pairing_code_audit_failure_rolls_back_code_and_project_snapshot(
    db_session: AsyncSession,
    active_owner: object,
    session_factory: async_sessionmaker[AsyncSession],
    test_settings: object,
    reference_time: datetime,
) -> None:
    """A pairing code without its authorization evidence must never become usable."""
    models, _service = device_contract()
    project = (await seed_projects(db_session, "Pairing audit rollback"))[0]
    service = service_for(session_factory, test_settings, reference_time)

    def fail_create(_mapper: object, _connection: object, target: AuditLog) -> None:
        if target.action == "device.pairing_code.create":
            raise RuntimeError("audit unavailable")

    event.listen(AuditLog, "before_insert", fail_create)
    try:
        with pytest.raises(RuntimeError, match="audit unavailable"):
            await service.create_pairing_code(
                active_owner.id, [project.id], request_id=uuid4()
            )
    finally:
        event.remove(AuditLog, "before_insert", fail_create)

    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(models.DevicePairingCode)) == 0
        assert await session.scalar(select(func.count()).select_from(models.DevicePairingProject)) == 0
