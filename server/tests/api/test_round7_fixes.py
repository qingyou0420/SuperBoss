"""Regression tests for the seventh review-fix round."""

from __future__ import annotations

import asyncio
import importlib.util
from collections.abc import AsyncIterator
from copy import deepcopy
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.community.postgres import PostgresContainer

from superboss.core.actors import Actor, get_actor
from superboss.core.db import get_session
from superboss.core.errors import DomainError
from superboss.modules.audit.models import AuditLog
from superboss.modules.knowledge.models import (
    KnowledgeRevision,
    KnowledgeRevisionPollution,
    KnowledgeRevisionReviewEvent,
    KnowledgeStatus,
)
from superboss.modules.knowledge.router import _to_read, get_service, router
from superboss.modules.knowledge.schemas import (
    KnowledgeDocCreate,
    KnowledgeDocUpdate,
    KnowledgePointWrite,
)
from superboss.modules.knowledge.service import KnowledgeService
from superboss.modules.users.models import Role
from tests.api.test_round6_fixes import (
    SERVER_ROOT,
    _drop_round6_audit,
    _fetch_schema,
    _migrate,
    _reset_schema,
)

_MIGRATION_0021 = SERVER_ROOT / "migrations" / "versions" / "0021_round7_integrity.py"
_SPEC = importlib.util.spec_from_file_location("migration_0021", _MIGRATION_0021)
assert _SPEC is not None and _SPEC.loader is not None
_MOD_0021 = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MOD_0021)
APPLY_LEGACY_CONFIRMATION_SQL = _MOD_0021.APPLY_LEGACY_CONFIRMATION_SQL

_AUDIT_SCHEMA_SQL = (
    """
    ALTER TABLE knowledge_revisions
        ADD COLUMN IF NOT EXISTS points_reviewed_by
        UUID REFERENCES users(id) ON DELETE SET NULL
    """,
    "ALTER TABLE knowledge_revisions ADD COLUMN IF NOT EXISTS points_reviewed_at TIMESTAMPTZ",
    """
    ALTER TABLE knowledge_revision_pollution
        ADD COLUMN IF NOT EXISTS reviewed_by
        UUID REFERENCES users(id) ON DELETE SET NULL
    """,
    """
    CREATE TABLE IF NOT EXISTS knowledge_revision_review_events (
        id UUID PRIMARY KEY,
        revision_id UUID NOT NULL
            REFERENCES knowledge_revisions(id) ON DELETE CASCADE,
        doc_id UUID NOT NULL
            REFERENCES knowledge_docs(id) ON DELETE CASCADE,
        action VARCHAR(16) NOT NULL,
        outcome VARCHAR(32) NOT NULL,
        actor_id UUID REFERENCES users(id) ON DELETE SET NULL,
        previous_status VARCHAR(16) NOT NULL,
        new_status VARCHAR(16) NOT NULL,
        original_points_json JSONB NOT NULL DEFAULT '[]'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_knowledge_revision_review_events_revision
        ON knowledge_revision_review_events (revision_id, created_at)
    """,
)


async def _insert_owner(url: str, username: str) -> UUID:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            return (
                await connection.execute(
                    text(
                        """
                        INSERT INTO users(
                            id, username, display_name, password_hash,
                            password_changed_at, role, status
                        )
                        VALUES (
                            gen_random_uuid(), :username, 'Owner',
                            '$argon2id$synthetic', now(), 'OWNER', 'ACTIVE'
                        )
                        RETURNING id
                        """
                    ),
                    {"username": username},
                )
            ).scalar_one()
    finally:
        await engine.dispose()


async def _publish_duplicate_points(
    session: AsyncSession,
    actor: Actor,
    title: str,
    body: str,
) -> tuple[UUID, UUID, list[dict[str, object]]]:
    service = KnowledgeService(session)
    doc = await service.create(
        actor,
        KnowledgeDocCreate(
            title=title,
            body_md="Original public body",
            points=[KnowledgePointWrite(title="Approved point", body_md=body)],
        ),
    )
    await service.update(actor, doc.id, KnowledgeDocUpdate(status=KnowledgeStatus.PUBLISHED))
    await service.update(
        actor,
        doc.id,
        KnowledgeDocUpdate(body_md="Updated draft body; points unchanged"),
    )
    loaded = await service.get(actor, doc.id)
    published = next(item for item in loaded.revisions if item.id == loaded.published_revision_id)
    return doc.id, published.id, deepcopy(list(published.points_json or []))


def _review_app(url: str, owner_id: UUID) -> tuple[FastAPI, object]:
    engine = create_async_engine(url)
    app = FastAPI()
    app.state.session_factory = async_sessionmaker(engine, expire_on_commit=False)
    app.include_router(router, prefix="/api/v1")

    @app.middleware("http")
    async def request_id(request: Request, call_next: object) -> object:
        request.state.request_id = str(uuid4())
        return await call_next(request)  # type: ignore[misc,operator]

    @app.exception_handler(DomainError)
    async def handle_domain(request: Request, error: DomainError) -> JSONResponse:
        del request
        return JSONResponse({"code": error.code}, status_code=error.status_code)

    async def actor() -> Actor:
        return Actor(subject_id=owner_id, role=Role.OWNER)

    app.dependency_overrides[get_actor] = actor
    return app, engine


async def _race_review(
    url: str,
    owner_id: UUID,
    doc_id: UUID,
    revision_id: UUID,
    first_action: str,
    second_action: str,
) -> dict[str, object]:
    app, engine = _review_app(url, owner_id)
    both_read = asyncio.Event()
    finish_first = asyncio.Event()
    exceptions: list[dict[str, str]] = []
    seen: list[str] = []

    async def session_dep(request: Request) -> AsyncIterator[AsyncSession]:
        label = request.headers["X-Race-Label"]
        async with AsyncSession(engine, expire_on_commit=False, info={"label": label}) as session:
            try:
                yield session
                await session.commit()
            except Exception as error:
                exceptions.append(
                    {
                        "request": label,
                        "type": type(error).__name__,
                        "message": str(error),
                    }
                )
                await session.rollback()
                raise

    class ScheduledService(KnowledgeService):
        async def get(self, actor: Actor, doc_id: UUID):
            doc = await super().get(actor, doc_id)
            if not self.session.info.get("has_read"):
                self.session.info["has_read"] = True
                seen.append(str(self.session.info["label"]))
                if len(seen) == 2:
                    both_read.set()
                await asyncio.wait_for(both_read.wait(), 15)
                if self.session.info["label"] == "second":
                    await asyncio.wait_for(finish_first.wait(), 15)
            return doc

    async def service_dep(session: AsyncSession = Depends(get_session)) -> KnowledgeService:
        return ScheduledService(session)

    app.dependency_overrides[get_session] = session_dep
    app.dependency_overrides[get_service] = service_dep

    async def post(client: httpx.AsyncClient, action: str, label: str) -> dict[str, object]:
        response = await client.post(
            f"/api/v1/knowledge/{doc_id}/revisions/{revision_id}/points-review",
            json={"action": action},
            headers={"X-Race-Label": label},
        )
        body: object = response.text
        if "json" in response.headers.get("content-type", ""):
            body = response.json()
        return {"status": response.status_code, "body": body}

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://synthetic.test",
        ) as client:
            first_task = asyncio.create_task(post(client, first_action, "first"))
            second_task = asyncio.create_task(post(client, second_action, "second"))
            first = await asyncio.wait_for(first_task, 25)
            finish_first.set()
            second = await asyncio.wait_for(second_task, 25)
        engine_events = create_async_engine(url)
        try:
            async with AsyncSession(engine_events, expire_on_commit=False) as session:
                events = list(
                    (
                        await session.scalars(
                            select(KnowledgeRevisionReviewEvent)
                            .where(KnowledgeRevisionReviewEvent.revision_id == revision_id)
                            .order_by(
                                KnowledgeRevisionReviewEvent.created_at,
                                KnowledgeRevisionReviewEvent.id,
                            )
                        )
                    ).all()
                )
                revision = await session.get(KnowledgeRevision, revision_id)
                pollution = await session.get(KnowledgeRevisionPollution, revision_id)
                audits = list(
                    (
                        await session.scalars(
                            select(AuditLog)
                            .where(AuditLog.object_id == revision_id)
                            .order_by(AuditLog.created_at, AuditLog.id)
                        )
                    ).all()
                )
        finally:
            await engine_events.dispose()
        return {
            "first": first,
            "second": second,
            "exceptions": exceptions,
            "events": events,
            "audits": audits,
            "revision": revision,
            "pollution": pollution,
        }
    finally:
        finish_first.set()
        both_read.set()
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_points_review_repeat_semantics(
    postgres_database: str, db_session: AsyncSession, active_owner
) -> None:
    actor = Actor(subject_id=active_owner.id, role=Role.OWNER)
    cases: dict[str, tuple[UUID, UUID, list[dict[str, object]]]] = {}
    for title in ("double-clear", "double-confirm", "confirm-after-clear"):
        doc = await KnowledgeService(db_session).create(
            actor,
            KnowledgeDocCreate(
                title=title,
                body_md="RACE_BODY",
                points=[KnowledgePointWrite(title="Race preserved", body_md="RACE_POINT")],
            ),
        )
        await KnowledgeService(db_session).update(
            actor, doc.id, KnowledgeDocUpdate(status=KnowledgeStatus.PUBLISHED)
        )
        loaded = await KnowledgeService(db_session).get(actor, doc.id)
        published = next(
            item for item in loaded.revisions if item.id == loaded.published_revision_id
        )
        published.points_review = "NEEDS_REVIEW"
        cases[title] = (doc.id, published.id, deepcopy(list(published.points_json or [])))
    await db_session.commit()
    owner_id = active_owner.id
    url = postgres_database

    double_clear = await _race_review(url, owner_id, *cases["double-clear"][:2], "clear", "clear")
    assert double_clear["first"]["status"] == 200
    assert double_clear["second"]["status"] == 200
    assert double_clear["exceptions"] == []
    clear_events = double_clear["events"]
    assert [item.action for item in clear_events] == ["clear", "clear"]
    assert {(item.outcome, item.previous_status, item.new_status) for item in clear_events} == {
        ("CLEARED", "NEEDS_REVIEW", "CLEARED"),
        ("ALREADY_CLEARED", "CLEARED", "CLEARED"),
    }
    revision = double_clear["revision"]
    pollution = double_clear["pollution"]
    assert revision is not None and pollution is not None
    assert revision.points_review == "CLEARED"
    assert revision.points_json == []
    assert pollution.original_points_json == cases["double-clear"][2]
    assert len(double_clear["audits"]) == 2

    double_confirm = await _race_review(
        url, owner_id, *cases["double-confirm"][:2], "confirm", "confirm"
    )
    assert double_confirm["first"]["status"] == 200
    assert double_confirm["second"]["status"] == 200
    confirm_events = double_confirm["events"]
    assert {(item.outcome, item.previous_status, item.new_status) for item in confirm_events} == {
        ("CONFIRMED", "NEEDS_REVIEW", "OK"),
        ("ALREADY_CONFIRMED", "OK", "OK"),
    }
    confirmed = double_confirm["revision"]
    assert confirmed is not None
    assert confirmed.points_review == "OK"
    assert confirmed.points_json == cases["double-confirm"][2]

    mixed = await _race_review(url, owner_id, *cases["confirm-after-clear"][:2], "clear", "confirm")
    assert mixed["first"]["status"] == 200
    assert mixed["second"]["status"] == 200
    mixed_events = mixed["events"]
    by_action = {item.action: item for item in mixed_events}
    assert by_action["clear"].outcome == "CLEARED"
    assert by_action["clear"].previous_status == "NEEDS_REVIEW"
    assert by_action["clear"].new_status == "CLEARED"
    assert by_action["confirm"].outcome == "CONFIRMED"
    assert by_action["confirm"].previous_status == "CLEARED"
    assert by_action["confirm"].new_status == "OK"
    mixed_revision = mixed["revision"]
    mixed_pollution = mixed["pollution"]
    assert mixed_revision is not None and mixed_pollution is not None
    assert mixed_revision.points_review == "OK"
    assert mixed_revision.points_json == []
    assert mixed_pollution.original_points_json == cases["confirm-after-clear"][2]


async def _unrecorded_old_0018(url: str) -> dict[str, object]:
    _migrate(url, "upgrade", "0018_round3_integrity")
    owner = await _insert_owner(url, "round7-unrecorded")
    engine = create_async_engine(url)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            actor = Actor(subject_id=owner, role=Role.OWNER)
            confirmed_id, confirmed_rev, confirmed_points = await _publish_duplicate_points(
                session, actor, "旧确认无记录", "APPROVED_RETAIN_ME"
            )
            pending_id, pending_rev, pending_points = await _publish_duplicate_points(
                session, actor, "从未核对", "STILL_SECRET"
            )
            await session.commit()
    finally:
        await engine.dispose()
    await _drop_round6_audit(url)
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("UPDATE knowledge_revisions SET points_review = 'OK' WHERE id = :id"),
                {"id": confirmed_rev},
            )
    finally:
        await engine.dispose()
    return {
        "owner": owner,
        "confirmed_id": confirmed_id,
        "confirmed_rev": confirmed_rev,
        "confirmed_points": confirmed_points,
        "pending_id": pending_id,
        "pending_rev": pending_rev,
        "pending_points": pending_points,
    }


@pytest.mark.asyncio
async def test_old_0018_unrecorded_confirm_is_listed_not_auto_restored() -> None:
    with PostgresContainer("postgres:17-alpine") as postgres:
        url = postgres.get_connection_url().replace("postgresql+psycopg2", "postgresql+asyncpg")
        fixture = await _unrecorded_old_0018(url)
        _migrate(url, "upgrade", "head")
        engine = create_async_engine(url)
        try:
            async with AsyncSession(engine, expire_on_commit=False) as session:
                actor = Actor(subject_id=fixture["owner"], role=Role.OWNER)
                staff = Actor(subject_id=fixture["owner"], role=Role.STAFF)
                confirmed = await KnowledgeService(session).get(actor, fixture["confirmed_id"])
                confirmed_pub = next(item for item in confirmed.revisions if item.version == 1)
                assert confirmed_pub.points_review == "NEEDS_REVIEW"
                assert confirmed_pub.points_json == fixture["confirmed_points"]
                assert not any(
                    point.body_md == "APPROVED_RETAIN_ME"
                    for point in _to_read(staff, confirmed).points
                )
                pending = await KnowledgeService(session).get(actor, fixture["pending_id"])
                pending_pub = next(item for item in pending.revisions if item.version == 1)
                assert pending_pub.points_review == "NEEDS_REVIEW"
                candidates = (
                    await session.execute(
                        text(
                            """
                            SELECT revision_id, recommended_action
                            FROM knowledge_unrecorded_ok_candidates
                            ORDER BY version, revision_id
                            """
                        )
                    )
                ).all()
                candidate_ids = {row[0] for row in candidates}
                assert fixture["confirmed_rev"] in candidate_ids
                assert fixture["pending_rev"] in candidate_ids
                assert all(
                    "Do not batch-update this list back to OK" in row[1] for row in candidates
                )
                await KnowledgeService(session).review_points(
                    actor, fixture["confirmed_id"], fixture["confirmed_rev"], "confirm"
                )
                await session.commit()
            async with AsyncSession(engine, expire_on_commit=False) as session:
                actor = Actor(subject_id=fixture["owner"], role=Role.OWNER)
                staff = Actor(subject_id=fixture["owner"], role=Role.STAFF)
                restored = await KnowledgeService(session).get(actor, fixture["confirmed_id"])
                restored_pub = next(item for item in restored.revisions if item.version == 1)
                assert restored_pub.points_review == "OK"
                assert restored_pub.points_reviewed_at is not None
                assert any(
                    point.body_md == "APPROVED_RETAIN_ME"
                    for point in _to_read(staff, restored).points
                )
                still_pending = await KnowledgeService(session).get(actor, fixture["pending_id"])
                still_pending_pub = next(
                    item for item in still_pending.revisions if item.version == 1
                )
                assert still_pending_pub.points_review == "NEEDS_REVIEW"
                event = await session.scalar(
                    select(KnowledgeRevisionReviewEvent).where(
                        KnowledgeRevisionReviewEvent.revision_id == fixture["confirmed_rev"],
                        KnowledgeRevisionReviewEvent.action == "confirm",
                    )
                )
                assert event is not None
                assert event.outcome == "CONFIRMED"
        finally:
            await engine.dispose()


@pytest.mark.asyncio
async def test_old_0018_schema_only_then_owner_confirm_survives_upgrade() -> None:
    with PostgresContainer("postgres:17-alpine") as postgres:
        url = postgres.get_connection_url().replace("postgresql+psycopg2", "postgresql+asyncpg")
        fixture = await _unrecorded_old_0018(url)
        engine = create_async_engine(url)
        try:
            async with engine.begin() as connection:
                for statement in _AUDIT_SCHEMA_SQL:
                    await connection.execute(text(statement))
            async with AsyncSession(engine, expire_on_commit=False) as session:
                actor = Actor(subject_id=fixture["owner"], role=Role.OWNER)
                await KnowledgeService(session).review_points(
                    actor, fixture["confirmed_id"], fixture["confirmed_rev"], "confirm"
                )
                await session.commit()
            _migrate(url, "upgrade", "head")
            async with AsyncSession(engine, expire_on_commit=False) as session:
                actor = Actor(subject_id=fixture["owner"], role=Role.OWNER)
                staff = Actor(subject_id=fixture["owner"], role=Role.STAFF)
                confirmed = await KnowledgeService(session).get(actor, fixture["confirmed_id"])
                confirmed_pub = next(item for item in confirmed.revisions if item.version == 1)
                assert confirmed_pub.points_review == "OK"
                assert confirmed_pub.points_json == fixture["confirmed_points"]
                assert any(
                    point.body_md == "APPROVED_RETAIN_ME"
                    for point in _to_read(staff, confirmed).points
                )
                pending = await KnowledgeService(session).get(actor, fixture["pending_id"])
                pending_pub = next(item for item in pending.revisions if item.version == 1)
                assert pending_pub.points_review == "NEEDS_REVIEW"
        finally:
            await engine.dispose()


@pytest.mark.asyncio
async def test_legacy_confirmation_map_restores_only_mapped_revision() -> None:
    with PostgresContainer("postgres:17-alpine") as postgres:
        url = postgres.get_connection_url().replace("postgresql+psycopg2", "postgresql+asyncpg")
        fixture = await _unrecorded_old_0018(url)
        _migrate(url, "upgrade", "head")
        engine = create_async_engine(url)
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        """
                        INSERT INTO knowledge_legacy_confirmation_map (
                            revision_id, confirmed_by, note
                        )
                        VALUES (:revision_id, :owner, 'trusted old 0018 confirm')
                        """
                    ),
                    {"revision_id": fixture["confirmed_rev"], "owner": fixture["owner"]},
                )
                await connection.execute(text(APPLY_LEGACY_CONFIRMATION_SQL))
            async with AsyncSession(engine, expire_on_commit=False) as session:
                actor = Actor(subject_id=fixture["owner"], role=Role.OWNER)
                staff = Actor(subject_id=fixture["owner"], role=Role.STAFF)
                confirmed = await KnowledgeService(session).get(actor, fixture["confirmed_id"])
                confirmed_pub = next(item for item in confirmed.revisions if item.version == 1)
                assert confirmed_pub.points_review == "OK"
                assert confirmed_pub.points_reviewed_by == fixture["owner"]
                assert confirmed_pub.points_json == fixture["confirmed_points"]
                assert any(
                    point.body_md == "APPROVED_RETAIN_ME"
                    for point in _to_read(staff, confirmed).points
                )
                pending = await KnowledgeService(session).get(actor, fixture["pending_id"])
                pending_pub = next(item for item in pending.revisions if item.version == 1)
                assert pending_pub.points_review == "NEEDS_REVIEW"
                assert not any(
                    point.body_md == "STILL_SECRET" for point in _to_read(staff, pending).points
                )
                event = await session.scalar(
                    select(KnowledgeRevisionReviewEvent).where(
                        KnowledgeRevisionReviewEvent.revision_id == fixture["confirmed_rev"],
                        KnowledgeRevisionReviewEvent.action == "confirm",
                    )
                )
                assert event is not None
                assert event.previous_status == "NEEDS_REVIEW"
                assert event.new_status == "OK"
                audit = await session.scalar(
                    select(AuditLog).where(
                        AuditLog.object_id == fixture["confirmed_rev"],
                        AuditLog.action == "knowledge.revision.points_review.confirm",
                    )
                )
                assert audit is not None
                assert audit.outcome == "CONFIRMED"
        finally:
            await engine.dispose()


@pytest.mark.asyncio
async def test_round7_migration_paths_share_candidate_schema() -> None:
    with PostgresContainer("postgres:17-alpine") as postgres:
        url = postgres.get_connection_url().replace("postgresql+psycopg2", "postgresql+asyncpg")
        _migrate(url, "upgrade", "head")
        empty = await _fetch_schema(url)
        await _reset_schema(url)
        _migrate(url, "upgrade", "0015_placeholder_data")
        _migrate(url, "upgrade", "head")
        historical = await _fetch_schema(url)
        await _reset_schema(url)
        _migrate(url, "upgrade", "0018_round3_integrity")
        await _drop_round6_audit(url)
        _migrate(url, "upgrade", "head")
        old_0018 = await _fetch_schema(url)
        await _reset_schema(url)
        _migrate(url, "upgrade", "0019_round5_integrity")
        await _drop_round6_audit(url)
        _migrate(url, "upgrade", "head")
        old_0019 = await _fetch_schema(url)
        assert empty == historical == old_0018 == old_0019
        names = {(item[0], item[1]) for item in empty["columns"]}
        assert ("knowledge_unrecorded_ok_candidates", "recommended_action") in names
        assert ("knowledge_legacy_confirmation_map", "confirmed_by") in names
