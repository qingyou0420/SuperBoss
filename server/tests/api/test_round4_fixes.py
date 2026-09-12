"""Regression tests for the fourth review-fix round."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.community.postgres import PostgresContainer

from superboss.core.actors import Actor
from superboss.core.config import Settings
from superboss.main import create_app
from superboss.modules.directory.models import DirectoryConflict, DirectoryImportBatch
from superboss.modules.directory.service import DirectoryService
from superboss.modules.finance.models import FinanceImportBatch, FinanceImportRow
from superboss.modules.finance.service import FinanceService
from superboss.modules.knowledge.models import KnowledgeStatus
from superboss.modules.knowledge.router import _to_read
from superboss.modules.knowledge.schemas import (
    KnowledgeDocCreate,
    KnowledgeDocUpdate,
    KnowledgePointWrite,
)
from superboss.modules.knowledge.service import KnowledgeService
from superboss.modules.users.models import Role, User
from tests.api.test_round3_fixes import _csrf, _login
from tests.directory.test_directory import _xlsx
from tests.files.storage import InMemoryObjectStorage

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


@pytest.mark.asyncio
async def test_legacy_filtered_index_import_recovers_missing_source_row(
    client: TestClient, db_session: AsyncSession, active_owner: User
) -> None:
    _login(client)
    created = client.post(
        "/api/v1/projects",
        json={"name": "历史行身份", "starts_on": "2026-09-01"},
        headers=_csrf(client),
    )
    project_id = created.json()["id"]
    first = client.post(
        "/api/v1/finance/import",
        json={
            "batch_key": "legacy-index",
            "rows": [
                {
                    "kind": "COST",
                    "scope": "PROJECT",
                    "project_id": project_id,
                    "amount_cents": 300,
                    "occurred_on": "2026-09-03",
                    "category": "印刷",
                    "source_row": 3,
                },
                {
                    "kind": "COST",
                    "scope": "PROJECT",
                    "project_id": project_id,
                    "amount_cents": 400,
                    "occurred_on": "2026-09-04",
                    "category": "印刷",
                    "source_row": 4,
                },
                {
                    "kind": "COST",
                    "scope": "PROJECT",
                    "project_id": project_id,
                    "amount_cents": 500,
                    "occurred_on": "2026-09-05",
                    "category": "印刷",
                    "source_row": 5,
                },
            ],
        },
        headers=_csrf(client),
    )
    assert first.json()["inserted"] == 3
    db_session.expire_all()
    stored = list(
        (
            await db_session.scalars(
                select(FinanceImportRow).where(FinanceImportRow.batch_key == "legacy-index")
            )
        ).all()
    )
    assert len(stored) == 3
    for index, item in enumerate(
        sorted(stored, key=lambda row: int((row.payload or {}).get("source_row") or 0))
    ):
        item.row_index = index
        payload = dict(item.payload or {})
        payload["source_row"] = index + 3
        payload["source_sheet"] = "Sheet1"
        item.payload = payload
    await db_session.commit()
    retry = client.post(
        "/api/v1/finance/import",
        json={
            "batch_key": "legacy-index",
            "rows": [
                {
                    "kind": "COST",
                    "scope": "PROJECT",
                    "project_id": project_id,
                    "amount_cents": 200,
                    "occurred_on": "2026-09-02",
                    "category": "印刷",
                    "source_row": 2,
                },
                {
                    "kind": "COST",
                    "scope": "PROJECT",
                    "project_id": project_id,
                    "amount_cents": 300,
                    "occurred_on": "2026-09-03",
                    "category": "印刷",
                    "source_row": 3,
                },
                {
                    "kind": "COST",
                    "scope": "PROJECT",
                    "project_id": project_id,
                    "amount_cents": 400,
                    "occurred_on": "2026-09-04",
                    "category": "印刷",
                    "source_row": 4,
                },
                {
                    "kind": "COST",
                    "scope": "PROJECT",
                    "project_id": project_id,
                    "amount_cents": 500,
                    "occurred_on": "2026-09-05",
                    "category": "印刷",
                    "source_row": 5,
                },
            ],
        },
        headers=_csrf(client),
    )
    body = retry.json()
    assert body["inserted"] == 1
    assert body["skipped"] == 3
    assert body["unresolved"] == []
    amounts = {
        item["amount_cents"]
        for item in client.get("/api/v1/finance/entries", params={"month": "2026-09"}).json()
        if item["project_id"] == project_id
    }
    assert amounts == {200, 300, 400, 500}


@pytest.mark.asyncio
async def test_changed_success_source_row_is_conflict(client: TestClient) -> None:
    _login(client)
    created = client.post(
        "/api/v1/projects",
        json={"name": "成功行变更", "starts_on": "2026-09-01"},
        headers=_csrf(client),
    )
    project_id = created.json()["id"]
    first = client.post(
        "/api/v1/finance/import",
        json={
            "batch_key": "content-change",
            "rows": [
                {
                    "kind": "COST",
                    "scope": "PROJECT",
                    "project_id": project_id,
                    "amount_cents": 1371,
                    "occurred_on": "2026-09-02",
                    "category": "印刷",
                    "memo": "备注A",
                    "source_row": 2,
                }
            ],
        },
        headers=_csrf(client),
    )
    assert first.json()["inserted"] == 1
    retry = client.post(
        "/api/v1/finance/import",
        json={
            "batch_key": "content-change",
            "rows": [
                {
                    "kind": "COST",
                    "scope": "PROJECT",
                    "project_id": project_id,
                    "amount_cents": 7319,
                    "occurred_on": "2026-09-02",
                    "category": "印刷",
                    "memo": "备注B",
                    "source_row": 2,
                }
            ],
        },
        headers=_csrf(client),
    )
    body = retry.json()
    assert body["inserted"] == 0
    assert body["skipped"] == 0
    assert body["unresolved"]
    assert body["unresolved"][0]["reason"] == "SUCCESS_CONTENT_MISMATCH"
    pending = client.get("/api/v1/finance/import-rows").json()
    reasons = {item["reason"] for item in pending["items"] if item["batch_key"] == "content-change"}
    assert "SUCCESS_CONTENT_MISMATCH" in reasons
    amounts = {
        item["amount_cents"]
        for item in client.get("/api/v1/finance/entries", params={"month": "2026-09"}).json()
        if item["project_id"] == project_id
    }
    assert amounts == {1371}


@pytest.mark.asyncio
async def test_resolve_independent_is_atomic(
    postgres_database: str, client: TestClient, db_session: AsyncSession, active_owner: User
) -> None:
    _login(client)
    created = client.post(
        "/api/v1/projects",
        json={"name": "并发入账", "starts_on": "2026-09-01"},
        headers=_csrf(client),
    )
    project_id = UUID(created.json()["id"])
    db_session.add(FinanceImportBatch(batch_key="resolve-lock", created_by=active_owner.id))
    await db_session.flush()
    db_session.add(
        FinanceImportRow(
            batch_key="resolve-lock",
            row_index=8,
            status="UNRESOLVED",
            reason="DUPLICATE_CANDIDATE",
            payload={
                "kind": "COST",
                "scope": "PROJECT",
                "project_id": str(project_id),
                "amount_cents": 25000,
                "occurred_on": "2026-09-02",
                "category": "外包",
                "source_row": 8,
            },
        )
    )
    await db_session.commit()
    engine = create_async_engine(postgres_database)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    actor = Actor(subject_id=active_owner.id, role=Role.OWNER)

    async def resolve_once() -> UUID:
        async with factory() as session:
            row = await FinanceService(session).resolve_import_row(
                actor, "resolve-lock", 8, "insert_independent"
            )
            await session.commit()
            assert row.entry_id is not None
            return row.entry_id

    try:
        first, second = await asyncio.gather(resolve_once(), resolve_once())
        assert first == second
        stored = (
            await db_session.execute(
                text("SELECT count(*) FROM finance_entries WHERE batch_key = 'resolve-lock'")
            )
        ).scalar_one()
        assert stored == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_link_voucher_rejects_existing_different_voucher(client: TestClient) -> None:
    _login(client)
    created = client.post(
        "/api/v1/projects",
        json={"name": "凭证冲突", "starts_on": "2026-09-01"},
        headers=_csrf(client),
    )
    project_id = created.json()["id"]
    first = client.post(
        "/api/v1/finance/import",
        json={
            "batch_key": "voucher-a",
            "rows": [
                {
                    "kind": "COST",
                    "scope": "PROJECT",
                    "project_id": project_id,
                    "amount_cents": 25000,
                    "occurred_on": "2026-09-02",
                    "category": "外包",
                    "memo": "同一备注",
                    "voucher": "r4-verified-voucher.png",
                    "source_row": 4,
                }
            ],
        },
        headers=_csrf(client),
    )
    assert first.json()["inserted"] == 1
    second = client.post(
        "/api/v1/finance/import",
        json={
            "batch_key": "voucher-b",
            "rows": [
                {
                    "kind": "COST",
                    "scope": "PROJECT",
                    "project_id": project_id,
                    "amount_cents": 25000,
                    "occurred_on": "2026-09-02",
                    "category": "外包",
                    "memo": "同一备注",
                    "voucher": "additional-evidence.png",
                    "source_row": 5,
                }
            ],
        },
        headers=_csrf(client),
    )
    unresolved = second.json()["unresolved"]
    linked = client.post(
        "/api/v1/finance/imports/voucher-b/rows/5/resolve",
        json={"action": "link_voucher", "entry_id": unresolved[0]["candidate_entry_id"]},
        headers=_csrf(client),
    )
    assert linked.status_code == 409
    assert linked.json()["error"]["code"] == "FINANCE_VOUCHER_CONFLICT"
    listed = client.get("/api/v1/finance/entries", params={"month": "2026-09"}).json()
    match = next(item for item in listed if item["amount_cents"] == 25000)
    assert match["voucher"] == "r4-verified-voucher.png"
    pending = client.get("/api/v1/finance/import-rows").json()
    assert any(
        item["batch_key"] == "voucher-b" and item["status"] == "UNRESOLVED"
        for item in pending["items"]
    )


@pytest.mark.asyncio
async def test_pay_key_rejects_changed_amount(client: TestClient) -> None:
    _login(client)
    created = client.post(
        "/api/v1/projects",
        json={"name": "付款变参", "starts_on": "2026-09-01"},
        headers=_csrf(client),
    )
    entry = client.post(
        "/api/v1/finance/entries",
        json={
            "kind": "COST",
            "scope": "PROJECT",
            "project_id": created.json()["id"],
            "amount_cents": 100_000,
            "occurred_on": "2026-09-02",
            "category": "印刷",
        },
        headers=_csrf(client),
    ).json()
    first = client.post(
        f"/api/v1/finance/entries/{entry['id']}/pay",
        json={
            "paid_on": "2026-08-19",
            "paid_cents": 40000,
            "idempotency_key": "pay-original",
        },
        headers=_csrf(client),
    )
    assert first.status_code == 200
    changed = client.post(
        f"/api/v1/finance/entries/{entry['id']}/pay",
        json={
            "paid_on": "2026-09-11",
            "paid_cents": 50000,
            "idempotency_key": "pay-original",
        },
        headers=_csrf(client),
    )
    assert changed.status_code == 409
    assert changed.json()["error"]["code"] == "FINANCE_PAY_KEY_MISMATCH"
    same = client.post(
        f"/api/v1/finance/entries/{entry['id']}/pay",
        json={
            "paid_on": "2026-08-19",
            "paid_cents": 40000,
            "idempotency_key": "pay-original",
        },
        headers=_csrf(client),
    )
    assert same.status_code == 200
    payments = same.json()["payments"]
    assert len(payments) == 1
    assert payments[0]["amount_cents"] == 40000
    assert payments[0]["paid_on"] == "2026-08-19"


@pytest.mark.asyncio
async def test_directory_same_source_candidates_are_exclusive(client: TestClient) -> None:
    _login(client)
    headers = [
        "物业小区名称",
        "县区",
        "小区所在街道 （乡镇）",
        "小区所在社区（村）",
        "物业企业全称",
        "总户数（户）",
        "总建筑面积（平方）",
        "联系手机",
    ]
    first = client.post(
        "/api/v1/directory/imports",
        files={
            "file": (
                "split.xlsx",
                _xlsx(
                    headers,
                    [
                        ["合成花园", "芗城区", "东铺头街道", "A社区", "", "100", "1000", ""],
                        ["合成花园", "芗城区", "东铺头街道", "A社区", "", "300", "3000", ""],
                    ],
                ),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
        headers=_csrf(client),
    )
    assert first.json()["inserted"] == 2
    second = client.post(
        "/api/v1/directory/imports",
        files={
            "file": (
                "uncertain.xlsx",
                _xlsx(
                    headers,
                    [
                        [
                            "合成花园",
                            "芗城区",
                            "东铺头街道",
                            "A社区",
                            "UNCERTAIN_COMPANY",
                            "",
                            "",
                            "",
                        ]
                    ],
                ),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
        headers=_csrf(client),
    )
    assert second.json()["inserted"] == 0
    pending = client.get("/api/v1/directory/conflicts").json()
    open_items = [item for item in pending["items"] if item["status"] == "OPEN"]
    assert len(open_items) >= 2
    first_id = open_items[0]["id"]
    second_id = open_items[1]["id"]
    linked = client.post(
        f"/api/v1/directory/conflicts/{first_id}/resolve",
        json={"action": "link"},
        headers=_csrf(client),
    )
    assert linked.status_code == 200
    assert linked.json()["status"] == "LINKED"
    later = client.post(
        f"/api/v1/directory/conflicts/{second_id}/resolve",
        json={"action": "link"},
        headers=_csrf(client),
    )
    assert later.status_code == 200
    assert later.json()["status"] == "SUPERSEDED"
    listed = client.get("/api/v1/directory").json()
    filled = [item for item in listed["items"] if item["property_company"] == "UNCERTAIN_COMPANY"]
    assert len(filled) == 1


@pytest.mark.asyncio
async def test_directory_conflict_pagination_is_unique(
    db_session: AsyncSession, active_owner: User
) -> None:
    actor = Actor(subject_id=active_owner.id, role=Role.OWNER)
    batch = DirectoryImportBatch(filename="page.xlsx", created_by=active_owner.id)
    db_session.add(batch)
    await db_session.flush()
    for index in range(205):
        db_session.add(
            DirectoryConflict(
                batch_id=batch.id,
                reason="IDENTITY_AMBIGUOUS",
                payload={"row": index},
                status="OPEN",
            )
        )
    await db_session.flush()
    service = DirectoryService(db_session)
    ids: list[UUID] = []
    for offset in (0, 50, 100, 150, 200):
        rows, total = await service.list_conflicts(actor, offset=offset, limit=50)
        assert total == 205
        ids.extend(item.id for item in rows)
    assert len(ids) == 205
    assert len(set(ids)) == 205


@pytest.mark.asyncio
async def test_0018_keeps_legitimate_points_and_clears_explicit_pollution() -> None:
    with PostgresContainer("postgres:17-alpine") as postgres:
        url = postgres.get_connection_url().replace("postgresql+psycopg2", "postgresql+asyncpg")
        _migrate(url, "upgrade", "head")
        engine = create_async_engine(url)
        owner = uuid4()
        polluted_v1 = uuid4()
        polluted_v2 = uuid4()
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        """
                        INSERT INTO users(
                            id, username, display_name, password_hash,
                            password_changed_at, role, status
                        )
                        VALUES (
                            :id, 'round4-owner', 'Owner',
                            '$argon2id$synthetic', now(), 'OWNER', 'ACTIVE'
                        )
                        """
                    ),
                    {"id": owner},
                )
            async with AsyncSession(engine, expire_on_commit=False) as session:
                actor = Actor(subject_id=owner, role=Role.OWNER)
                service = KnowledgeService(session)
                doc = await service.create(
                    actor,
                    KnowledgeDocCreate(
                        title="Legitimate published knowledge",
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
                await session.commit()
                legitimate_id = doc.id
            _migrate(url, "downgrade", "0017_round2_constraints")
            async with engine.begin() as connection:
                polluted_doc = uuid4()
                secret = [
                    {
                        "id": str(uuid4()),
                        "title": "secret",
                        "body_md": "UNPUBLISHED_OLD0017_SECRET",
                        "source_file_id": None,
                        "sort_order": 0,
                    }
                ]
                await connection.execute(
                    text(
                        """
                        INSERT INTO knowledge_docs(
                            id, title, body_md, tags, status, stage_title,
                            change_reason, is_canonical, created_by
                        )
                        VALUES (
                            :id, 'old0017_polluted_copies', 'CURRENT_BODY',
                            ARRAY[]::varchar[], 'PUBLISHED', '', '', false, :owner
                        )
                        """
                    ),
                    {"id": polluted_doc, "owner": owner},
                )
                for revision_id, version, body in (
                    (polluted_v1, 1, "BODY_VERSION_1"),
                    (polluted_v2, 2, "BODY_VERSION_2"),
                ):
                    await connection.execute(
                        text(
                            """
                            INSERT INTO knowledge_revisions(
                                id, doc_id, version, body_md, created_by, points_json
                            )
                            VALUES (:id, :doc_id, :version, :body, :owner, CAST(:points AS jsonb))
                            """
                        ),
                        {
                            "id": revision_id,
                            "doc_id": polluted_doc,
                            "version": version,
                            "body": body,
                            "owner": owner,
                            "points": json.dumps(secret),
                        },
                    )
                await connection.execute(
                    text(
                        """
                        UPDATE knowledge_docs
                        SET published_revision_id = :v1, draft_revision_id = :v2
                        WHERE id = :doc
                        """
                    ),
                    {"v1": polluted_v1, "v2": polluted_v2, "doc": polluted_doc},
                )
                await connection.execute(
                    text(
                        """
                        CREATE TABLE knowledge_revision_pollution_staging (
                            revision_id UUID PRIMARY KEY
                        )
                        """
                    )
                )
                await connection.execute(
                    text(
                        "INSERT INTO knowledge_revision_pollution_staging(revision_id) VALUES (:id)"
                    ),
                    {"id": polluted_v1},
                )
            _migrate(url, "upgrade", "head")
            async with AsyncSession(engine, expire_on_commit=False) as session:
                actor = Actor(subject_id=owner, role=Role.OWNER)
                staff = Actor(subject_id=owner, role=Role.STAFF)
                legit = await KnowledgeService(session).get(actor, legitimate_id)
                published = next(item for item in legit.revisions if item.version == 1)
                assert published.points_json
                assert published.points_json[0]["body_md"] == "APPROVED_RETAIN_ME"
                assert published.points_review == "NEEDS_REVIEW"
                owner_payload = _to_read(actor, legit)
                assert any(point.body_md == "APPROVED_RETAIN_ME" for point in owner_payload.points)
                staff_payload = _to_read(staff, legit)
                assert not any(
                    point.body_md == "APPROVED_RETAIN_ME" for point in staff_payload.points
                )
                confirmed = await KnowledgeService(session).review_points(
                    actor, legitimate_id, published.id, "confirm"
                )
                staff_after = _to_read(staff, confirmed)
                assert any(point.body_md == "APPROVED_RETAIN_ME" for point in staff_after.points)
                polluted_points = (
                    await session.execute(
                        text(
                            "SELECT points_json, points_review FROM knowledge_revisions WHERE id = :id"
                        ),
                        {"id": polluted_v1},
                    )
                ).one()
                assert polluted_points[0] == [] or polluted_points[0] == "[]"
                assert polluted_points[1] == "CLEARED"
                draft_points = (
                    await session.execute(
                        text("SELECT points_json FROM knowledge_revisions WHERE id = :id"),
                        {"id": polluted_v2},
                    )
                ).scalar_one()
                assert draft_points
        finally:
            await engine.dispose()
