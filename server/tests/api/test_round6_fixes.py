"""Regression tests for the sixth review-fix round."""

from __future__ import annotations

import os
import subprocess
import sys
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape
from zipfile import ZipFile

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from testcontainers.community.postgres import PostgresContainer

from superboss.core.actors import Actor
from superboss.core.config import Settings
from superboss.main import create_app
from superboss.modules.audit.models import AuditLog
from superboss.modules.finance.models import FinanceEntry, FinanceImportRow
from superboss.modules.knowledge.models import (
    KnowledgeRevision,
    KnowledgeRevisionPollution,
    KnowledgeRevisionReviewEvent,
    KnowledgeStatus,
)
from superboss.modules.knowledge.router import _to_read
from superboss.modules.knowledge.schemas import (
    KnowledgeDocCreate,
    KnowledgeDocUpdate,
    KnowledgePointWrite,
)
from superboss.modules.knowledge.service import KnowledgeService
from superboss.modules.users.models import Role, User
from tests.api.test_round3_fixes import _csrf, _login
from tests.files.storage import InMemoryObjectStorage
from tests.identity import local_user

SERVER_ROOT = Path(__file__).resolve().parents[2]


def _migrate(database_url: str, *args: str) -> None:
    environment = os.environ.copy()
    environment["SUPERBOSS_DATABASE_URL"] = database_url
    subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=SERVER_ROOT,
        env=environment,
        check=True,
    )


def _workbook(row2_amount: str, row3_amount: str = "") -> bytes:
    rows = [
        ["类型", "范围", "日期", "金额", "类别", "项目", "备注"],
        ["费用", "公司", "2026-09-03", row2_amount, "synthetic", "", "same-source"],
        ["费用", "公司", "2026-09-03", row3_amount, "synthetic", "", "incomplete-retry-anchor"],
    ]
    stream = BytesIO()
    xml: list[str] = []
    for number, values in enumerate(rows, 1):
        cells = "".join(
            f'<c r="{chr(65 + index)}{number}" t="inlineStr"><is><t>{escape(value)}</t></is></c>'
            for index, value in enumerate(values)
        )
        xml.append(f'<row r="{number}">{cells}</row>')
    with ZipFile(stream, "w") as archive:
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f"<sheetData>{''.join(xml)}</sheetData></worksheet>",
        )
    return stream.getvalue()


@pytest_asyncio.fixture
async def client(db_session: AsyncSession, test_settings: Settings, active_owner: User):
    del active_owner
    await db_session.commit()
    app = create_app(
        test_settings,
        object_storage=InMemoryObjectStorage(),
        enqueue_file_scan=lambda _file_id, _delivery_key: None,
    )
    with TestClient(app, base_url="https://testserver") as test_client:
        yield test_client


def _import_xlsx(client: TestClient, amount: str, row3: str = "") -> dict[str, object]:
    response = client.post(
        "/api/v1/finance/imports",
        files={
            "file": (
                "synthetic.xlsx",
                _workbook(amount, row3),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
        params={"batch_key": "parse-retry"},
        headers=_csrf(client),
    )
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, dict)
    return body


@pytest.mark.asyncio
async def test_parse_failure_does_not_erase_success_identity(
    client: TestClient, db_session: AsyncSession
) -> None:
    _login(client)
    first = _import_xlsx(client, "250")
    assert first["inserted"] == 1
    second = _import_xlsx(client, "350")
    assert second["inserted"] == 0
    third = _import_xlsx(client, "")
    assert third["inserted"] == 0
    fourth = _import_xlsx(client, "350")
    assert fourth["inserted"] == 0
    amounts = {
        item["amount_cents"]
        for item in client.get("/api/v1/finance/entries", params={"month": "2026-09"}).json()
    }
    assert amounts == {25000}
    db_session.expire_all()
    stored = list(
        (
            await db_session.scalars(
                select(FinanceImportRow).where(FinanceImportRow.batch_key == "parse-retry")
            )
        ).all()
    )
    source = next(item for item in stored if item.row_index == 2)
    assert source.entry_id is not None
    assert source.reason == "SUCCESS_CONTENT_MISMATCH"
    assert (source.payload or {}).get("amount_cents") == 25000
    assert (source.payload or {}).get("pending_amount_cents") == 35000
    incomplete = next(item for item in stored if item.row_index == 3)
    assert incomplete.reason == "ROW_INCOMPLETE"
    recovered = _import_xlsx(client, "250", "11")
    assert recovered["inserted"] == 1
    db_session.expire_all()
    later_amounts = {
        item.amount_cents
        for item in (
            await db_session.scalars(
                select(FinanceEntry).where(FinanceEntry.batch_key == "parse-retry")
            )
        ).all()
    }
    assert later_amounts == {25000, 1100}


@pytest.mark.asyncio
async def test_points_review_records_operator_and_repeat_semantics(
    client: TestClient, db_session: AsyncSession, active_owner: User
) -> None:
    staff = local_user("review-audit-staff", display_name="Staff", role=Role.STAFF)
    db_session.add(staff)
    await db_session.commit()
    _login(client)
    actor = Actor(subject_id=active_owner.id, role=Role.OWNER)
    service = KnowledgeService(db_session)
    doc = await service.create(
        actor,
        KnowledgeDocCreate(
            title="核对审计",
            body_md="SAFE_BODY",
            stage_title="SAFE_STAGE",
            points=[KnowledgePointWrite(title="保留点", body_md="APPROVED_RETAIN_ME")],
        ),
    )
    await service.update(actor, doc.id, KnowledgeDocUpdate(status=KnowledgeStatus.PUBLISHED))
    loaded = await service.get(actor, doc.id)
    published = next(item for item in loaded.revisions if item.id == loaded.published_revision_id)
    published.points_review = "NEEDS_REVIEW"
    doc_id = doc.id
    revision_id = published.id
    owner_id = active_owner.id
    await db_session.commit()
    _login(client, "review-audit-staff")
    denied = client.post(
        f"/api/v1/knowledge/{doc_id}/revisions/{revision_id}/points-review",
        json={"action": "confirm"},
        headers=_csrf(client),
    )
    assert denied.status_code == 403
    _login(client)
    confirmed = client.post(
        f"/api/v1/knowledge/{doc_id}/revisions/{revision_id}/points-review",
        json={"action": "confirm"},
        headers=_csrf(client),
    )
    assert confirmed.status_code == 200
    repeat = client.post(
        f"/api/v1/knowledge/{doc_id}/revisions/{revision_id}/points-review",
        json={"action": "confirm"},
        headers=_csrf(client),
    )
    assert repeat.status_code == 200
    db_session.expire_all()
    events = list(
        (
            await db_session.scalars(
                select(KnowledgeRevisionReviewEvent)
                .where(KnowledgeRevisionReviewEvent.revision_id == revision_id)
                .order_by(KnowledgeRevisionReviewEvent.created_at)
            )
        ).all()
    )
    assert [item.action for item in events] == ["confirm", "confirm"]
    assert [item.outcome for item in events] == ["CONFIRMED", "ALREADY_CONFIRMED"]
    assert events[0].actor_id == owner_id
    assert events[0].previous_status == "NEEDS_REVIEW"
    assert events[0].new_status == "OK"
    audits = list(
        (
            await db_session.scalars(
                select(AuditLog).where(
                    AuditLog.action == "knowledge.revision.points_review.confirm"
                )
            )
        ).all()
    )
    assert len(audits) == 2
    assert {item.actor_id for item in audits} == {owner_id}
    assert {item.object_id for item in audits} == {revision_id}
    revision = await db_session.get(KnowledgeRevision, revision_id)
    assert revision is not None
    assert revision.points_reviewed_by == owner_id
    assert revision.points_reviewed_at is not None
    cleared = client.post(
        f"/api/v1/knowledge/{doc_id}/revisions/{revision_id}/points-review",
        json={"action": "clear"},
        headers=_csrf(client),
    )
    assert cleared.status_code == 200
    again = client.post(
        f"/api/v1/knowledge/{doc_id}/revisions/{revision_id}/points-review",
        json={"action": "clear"},
        headers=_csrf(client),
    )
    assert again.status_code == 200
    db_session.expire_all()
    pollution = await db_session.get(KnowledgeRevisionPollution, revision_id)
    assert pollution is not None
    assert pollution.reviewed_by == owner_id
    assert pollution.reason == "OWNER_CLEAR"
    clear_events = [
        item
        for item in (
            await db_session.scalars(
                select(KnowledgeRevisionReviewEvent)
                .where(KnowledgeRevisionReviewEvent.revision_id == revision_id)
                .order_by(KnowledgeRevisionReviewEvent.created_at)
            )
        ).all()
        if item.action == "clear"
    ]
    assert [item.outcome for item in clear_events] == ["CLEARED", "ALREADY_CLEARED"]
    assert pollution.original_points_json
    assert pollution.original_points_json[0]["body_md"] == "APPROVED_RETAIN_ME"
    assert clear_events[0].original_points_json == pollution.original_points_json


@pytest.mark.asyncio
async def test_staff_search_uses_readable_fallback_revision(
    client: TestClient, db_session: AsyncSession, active_owner: User
) -> None:
    staff = local_user("fallback-search-staff", display_name="Staff", role=Role.STAFF)
    db_session.add(staff)
    await db_session.commit()
    _login(client)
    actor = Actor(subject_id=active_owner.id, role=Role.OWNER)
    service = KnowledgeService(db_session)
    doc = await service.create(
        actor,
        KnowledgeDocCreate(
            title="回退检索",
            body_md="SAFE_SEARCH_BODY",
            stage_title="SAFE_STAGE",
            change_reason="SAFE_REASON",
        ),
    )
    await service.update(actor, doc.id, KnowledgeDocUpdate(status=KnowledgeStatus.PUBLISHED))
    await service.update(
        actor,
        doc.id,
        KnowledgeDocUpdate(
            body_md="ISOLATED_V2_BODY",
            stage_title="ISOLATED_STAGE",
            change_reason="ISOLATED_REASON",
        ),
    )
    await service.update(actor, doc.id, KnowledgeDocUpdate(status=KnowledgeStatus.PUBLISHED))
    loaded = await service.get(actor, doc.id)
    published = next(item for item in loaded.revisions if item.id == loaded.published_revision_id)
    published.points_review = "NEEDS_REVIEW"
    await db_session.commit()
    _login(client, "fallback-search-staff")
    detail = client.get(f"/api/v1/knowledge/{doc.id}")
    assert detail.status_code == 200
    assert "SAFE_SEARCH_BODY" in detail.text
    assert "ISOLATED_V2_BODY" not in detail.text
    by_body = client.get("/api/v1/knowledge", params={"q": "SAFE_SEARCH_BODY"})
    assert by_body.status_code == 200
    assert any(item["id"] == str(doc.id) for item in by_body.json())
    by_stage = client.get("/api/v1/knowledge", params={"stage": "SAFE_STAGE"})
    assert by_stage.status_code == 200
    assert any(item["id"] == str(doc.id) for item in by_stage.json())
    hidden_body = client.get("/api/v1/knowledge", params={"q": "ISOLATED_V2_BODY"})
    assert hidden_body.json() == []
    hidden_stage = client.get("/api/v1/knowledge", params={"stage": "ISOLATED_STAGE"})
    assert hidden_stage.json() == []
    staff_actor = Actor(subject_id=staff.id, role=Role.STAFF)
    hits = await KnowledgeService(db_session).search(staff_actor, "SAFE_SEARCH_BODY")
    assert any(item["id"] == str(doc.id) for item in hits)
    isolated_hits = await KnowledgeService(db_session).search(staff_actor, "ISOLATED_V2_BODY")
    assert isolated_hits == []


@pytest.mark.asyncio
async def test_0019_keeps_owner_confirmed_revision_visible() -> None:
    with PostgresContainer("postgres:17-alpine") as postgres:
        url = postgres.get_connection_url().replace("postgresql+psycopg2", "postgresql+asyncpg")
        _migrate(url, "upgrade", "0018_round3_integrity")
        engine = create_async_engine(url)
        try:
            async with engine.begin() as connection:
                owner = (
                    await connection.execute(
                        text(
                            """
                            INSERT INTO users(
                                id, username, display_name, password_hash,
                                password_changed_at, role, status
                            )
                            VALUES (
                                gen_random_uuid(), 'round6-owner', 'Owner',
                                '$argon2id$synthetic', now(), 'OWNER', 'ACTIVE'
                            )
                            RETURNING id
                            """
                        )
                    )
                ).scalar_one()
            async with AsyncSession(engine, expire_on_commit=False) as session:
                actor = Actor(subject_id=owner, role=Role.OWNER)
                service = KnowledgeService(session)
                doc = await service.create(
                    actor,
                    KnowledgeDocCreate(
                        title="已确认合法版",
                        body_md="Original public body",
                        points=[
                            KnowledgePointWrite(
                                title="Approved point", body_md="APPROVED_RETAIN_ME"
                            )
                        ],
                    ),
                )
                await service.update(
                    actor, doc.id, KnowledgeDocUpdate(status=KnowledgeStatus.PUBLISHED)
                )
                await service.update(
                    actor,
                    doc.id,
                    KnowledgeDocUpdate(body_md="Updated draft body; points unchanged"),
                )
                unchecked = await service.create(
                    actor,
                    KnowledgeDocCreate(
                        title="从未核对",
                        body_md="Original public body",
                        points=[KnowledgePointWrite(title="Pending point", body_md="STILL_SECRET")],
                    ),
                )
                await service.update(
                    actor, unchecked.id, KnowledgeDocUpdate(status=KnowledgeStatus.PUBLISHED)
                )
                await service.update(
                    actor,
                    unchecked.id,
                    KnowledgeDocUpdate(body_md="Draft only; points unchanged"),
                )
                loaded = await service.get(actor, doc.id)
                published = next(
                    item for item in loaded.revisions if item.id == loaded.published_revision_id
                )
                await service.review_points(actor, doc.id, published.id, "confirm")
                await session.commit()
                confirmed_id = doc.id
                unchecked_id = unchecked.id
            _migrate(url, "upgrade", "head")
            async with AsyncSession(engine, expire_on_commit=False) as session:
                actor = Actor(subject_id=owner, role=Role.OWNER)
                staff = Actor(subject_id=owner, role=Role.STAFF)
                confirmed = await KnowledgeService(session).get(actor, confirmed_id)
                confirmed_pub = next(item for item in confirmed.revisions if item.version == 1)
                assert confirmed_pub.points_review == "OK"
                assert confirmed_pub.points_json[0]["body_md"] == "APPROVED_RETAIN_ME"
                staff_confirmed = _to_read(staff, confirmed)
                assert any(
                    point.body_md == "APPROVED_RETAIN_ME" for point in staff_confirmed.points
                )
                pending = await KnowledgeService(session).get(actor, unchecked_id)
                pending_pub = next(item for item in pending.revisions if item.version == 1)
                assert pending_pub.points_review == "NEEDS_REVIEW"
                staff_pending = _to_read(staff, pending)
                assert not any(point.body_md == "STILL_SECRET" for point in staff_pending.points)
        finally:
            await engine.dispose()


def _schema_snapshot_sql() -> tuple[str, str, str]:
    columns = """
        SELECT table_name, column_name, data_type, is_nullable, column_default
        FROM information_schema.columns
        WHERE table_schema = 'public'
        ORDER BY table_name, column_name
    """
    constraints = """
        SELECT c.conrelid::regclass::text, c.conname, pg_get_constraintdef(c.oid)
        FROM pg_constraint c
        JOIN pg_namespace n ON n.oid = c.connamespace
        WHERE n.nspname = 'public'
        ORDER BY 1, 2, 3
    """
    indexes = """
        SELECT tablename, indexname, indexdef
        FROM pg_indexes
        WHERE schemaname = 'public'
        ORDER BY 1, 2, 3
    """
    return columns, constraints, indexes


async def _fetch_schema(url: str) -> dict[str, list[tuple[object, ...]]]:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            columns, constraints, indexes = _schema_snapshot_sql()
            return {
                "columns": [tuple(row) for row in (await connection.execute(text(columns))).all()],
                "constraints": [
                    tuple(row) for row in (await connection.execute(text(constraints))).all()
                ],
                "indexes": [tuple(row) for row in (await connection.execute(text(indexes))).all()],
            }
    finally:
        await engine.dispose()


async def _reset_schema(url: str) -> None:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("DROP SCHEMA public CASCADE"))
            await connection.execute(text("CREATE SCHEMA public"))
            await connection.execute(text("GRANT ALL ON SCHEMA public TO public"))
    finally:
        await engine.dispose()


async def _drop_round6_audit(url: str) -> None:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("DROP TABLE IF EXISTS knowledge_revision_review_events"))
            await connection.execute(
                text("ALTER TABLE knowledge_revision_pollution DROP COLUMN IF EXISTS reviewed_by")
            )
            await connection.execute(
                text("ALTER TABLE knowledge_revisions DROP COLUMN IF EXISTS points_reviewed_at")
            )
            await connection.execute(
                text("ALTER TABLE knowledge_revisions DROP COLUMN IF EXISTS points_reviewed_by")
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_round6_migration_paths_share_review_audit_schema() -> None:
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
        column_names = {(item[0], item[1]) for item in empty["columns"]}
        assert ("knowledge_revisions", "points_reviewed_by") in column_names
        assert ("knowledge_revisions", "points_reviewed_at") in column_names
        assert ("knowledge_revision_pollution", "reviewed_by") in column_names
        assert ("knowledge_revision_review_events", "id") in column_names
        assert ("knowledge_unrecorded_ok_candidates", "revision_id") in column_names
        assert ("knowledge_legacy_confirmation_map", "revision_id") in column_names
