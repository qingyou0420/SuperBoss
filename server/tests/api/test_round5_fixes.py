"""Regression tests for the fifth review-fix round."""

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
from superboss.modules.files.models import FolderVisibility
from superboss.modules.finance.models import FinanceImportBatch, FinanceImportRow
from superboss.modules.finance.service import FinanceService
from superboss.modules.knowledge.models import KnowledgeStatus
from superboss.modules.knowledge.schemas import (
    KnowledgeDocCreate,
    KnowledgeDocUpdate,
    KnowledgeIngestCard,
    KnowledgePointWrite,
)
from superboss.modules.knowledge.service import KnowledgeService
from superboss.modules.users.models import Role, User
from tests.api.test_round3_fixes import _csrf, _login
from tests.directory.test_directory import _xlsx
from tests.files.factory import add_folder, make_file
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
async def test_success_content_mismatch_retry_does_not_insert_second_entry(
    client: TestClient,
) -> None:
    _login(client)
    created = client.post(
        "/api/v1/projects",
        json={"name": "变参冲突", "starts_on": "2026-09-01"},
        headers=_csrf(client),
    )
    project_id = created.json()["id"]
    first = client.post(
        "/api/v1/finance/import",
        json={
            "batch_key": "repeat-mismatch",
            "rows": [
                {
                    "kind": "COST",
                    "scope": "PROJECT",
                    "project_id": project_id,
                    "amount_cents": 25000,
                    "occurred_on": "2026-09-02",
                    "category": "外包",
                    "source_row": 4,
                }
            ],
        },
        headers=_csrf(client),
    )
    assert first.json()["inserted"] == 1
    changed = {
        "batch_key": "repeat-mismatch",
        "rows": [
            {
                "kind": "COST",
                "scope": "PROJECT",
                "project_id": project_id,
                "amount_cents": 35000,
                "occurred_on": "2026-09-02",
                "category": "外包",
                "source_row": 4,
            }
        ],
    }
    second = client.post("/api/v1/finance/import", json=changed, headers=_csrf(client))
    assert second.json()["inserted"] == 0
    assert second.json()["unresolved"][0]["reason"] == "SUCCESS_CONTENT_MISMATCH"
    third = client.post("/api/v1/finance/import", json=changed, headers=_csrf(client))
    assert third.json()["inserted"] == 0
    assert third.json()["unresolved"][0]["reason"] == "SUCCESS_CONTENT_MISMATCH"
    amounts = {
        item["amount_cents"]
        for item in client.get("/api/v1/finance/entries", params={"month": "2026-09"}).json()
        if item["project_id"] == project_id
    }
    assert amounts == {25000}


@pytest.mark.asyncio
async def test_identical_project_name_replay_is_not_content_mismatch(client: TestClient) -> None:
    _login(client)
    created = client.post(
        "/api/v1/projects",
        json={"name": "名称重试", "starts_on": "2026-09-01"},
        headers=_csrf(client),
    )
    assert created.status_code == 201
    first = client.post(
        "/api/v1/finance/import",
        json={
            "batch_key": "name-replay",
            "rows": [
                {
                    "kind": "COST",
                    "scope": "PROJECT",
                    "project_name": "名称重试",
                    "amount_cents": 18000,
                    "occurred_on": "2026-09-03",
                    "category": "印刷",
                    "source_row": 2,
                }
            ],
        },
        headers=_csrf(client),
    )
    assert first.json()["inserted"] == 1
    replay = client.post(
        "/api/v1/finance/import",
        json={
            "batch_key": "name-replay",
            "rows": [
                {
                    "kind": "COST",
                    "scope": "PROJECT",
                    "project_name": "名称重试",
                    "amount_cents": 18000,
                    "occurred_on": "2026-09-03",
                    "category": "印刷",
                    "source_row": 2,
                }
            ],
        },
        headers=_csrf(client),
    )
    body = replay.json()
    assert body["inserted"] == 0
    assert body["skipped"] == 1
    assert body["unresolved"] == []


@pytest.mark.asyncio
async def test_ambiguous_legacy_source_rows_report_conflict(
    client: TestClient, db_session: AsyncSession, active_owner: User
) -> None:
    _login(client)
    created = client.post(
        "/api/v1/projects",
        json={"name": "歧义来源", "starts_on": "2026-09-01"},
        headers=_csrf(client),
    )
    project_id = created.json()["id"]
    db_session.add(FinanceImportBatch(batch_key="ambiguous-src", created_by=active_owner.id))
    await db_session.flush()
    for index in (0, 1):
        db_session.add(
            FinanceImportRow(
                batch_key="ambiguous-src",
                row_index=index,
                status="INSERTED",
                payload={
                    "source_row": 3,
                    "source_sheet": "Sheet1",
                    "project_id": project_id,
                    "amount_cents": 1000 + index,
                    "occurred_on": "2026-09-03",
                    "category": "印刷",
                    "kind": "COST",
                    "scope": "PROJECT",
                },
            )
        )
    await db_session.commit()
    retry = client.post(
        "/api/v1/finance/import",
        json={
            "batch_key": "ambiguous-src",
            "rows": [
                {
                    "kind": "COST",
                    "scope": "PROJECT",
                    "project_id": project_id,
                    "amount_cents": 3000,
                    "occurred_on": "2026-09-03",
                    "category": "印刷",
                    "source_row": 3,
                    "source_sheet": "Sheet1",
                }
            ],
        },
        headers=_csrf(client),
    )
    assert retry.status_code == 200
    body = retry.json()
    assert body["inserted"] == 0
    assert body["unresolved"]
    assert body["unresolved"][0]["reason"] == "SOURCE_IDENTITY_CONFLICT"


@pytest.mark.asyncio
async def test_resolve_preloaded_source_row_is_atomic(
    postgres_database: str, client: TestClient, db_session: AsyncSession, active_owner: User
) -> None:
    _login(client)
    created = client.post(
        "/api/v1/projects",
        json={"name": "预加载锁", "starts_on": "2026-09-01"},
        headers=_csrf(client),
    )
    project_id = UUID(created.json()["id"])
    db_session.add(FinanceImportBatch(batch_key="resolve-preload", created_by=active_owner.id))
    await db_session.flush()
    db_session.add(
        FinanceImportRow(
            batch_key="resolve-preload",
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
            loaded = await session.scalar(
                select(FinanceImportRow).where(
                    FinanceImportRow.batch_key == "resolve-preload",
                    FinanceImportRow.row_index == 8,
                )
            )
            assert loaded is not None
            row = await FinanceService(session).resolve_import_row(
                actor, "resolve-preload", 8, "insert_independent"
            )
            await session.commit()
            assert row.entry_id is not None
            return row.entry_id

    try:
        first, second = await asyncio.gather(resolve_once(), resolve_once())
        assert first == second
        stored = (
            await db_session.execute(
                text("SELECT count(*) FROM finance_entries WHERE batch_key = 'resolve-preload'")
            )
        ).scalar_one()
        assert stored == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_same_batch_first_link_confirms_existing_entry(client: TestClient) -> None:
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
    imported = client.post(
        "/api/v1/directory/imports",
        files={
            "file": (
                "same-batch.xlsx",
                _xlsx(
                    headers,
                    [
                        ["合成花园", "芗城区", "东铺头街道", "A社区", "", "100", "1000", ""],
                        ["合成花园", "芗城区", "东铺头街道", "A社区", "", "200", "2000", ""],
                    ],
                ),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
        headers=_csrf(client),
    )
    assert imported.json()["inserted"] == 2
    pending = client.get("/api/v1/directory/conflicts").json()
    open_items = [item for item in pending["items"] if item["status"] == "OPEN"]
    assert len(open_items) == 1
    linked = client.post(
        f"/api/v1/directory/conflicts/{open_items[0]['id']}/resolve",
        json={"action": "link"},
        headers=_csrf(client),
    )
    assert linked.status_code == 200
    assert linked.json()["status"] == "LINKED"
    remaining = client.get("/api/v1/directory/conflicts").json()
    assert all(item["id"] != open_items[0]["id"] for item in remaining["items"])


@pytest.mark.asyncio
async def test_needs_review_hides_secret_and_private_source_from_staff(
    client: TestClient, db_session: AsyncSession, active_owner: User
) -> None:
    staff = local_user("review-staff", display_name="Staff", role=Role.STAFF)
    db_session.add(staff)
    folder = await add_folder(
        db_session, active_owner.id, name="内部", visibility=FolderVisibility.OWNER_ONLY
    )
    secret_file = make_file(folder_id=folder.id, uploader_id=active_owner.id, filename="secret.pdf")
    db_session.add(secret_file)
    await db_session.commit()
    _login(client)
    actor = Actor(subject_id=active_owner.id, role=Role.OWNER)
    service = KnowledgeService(db_session)
    doc = await service.create(
        actor,
        KnowledgeDocCreate(title="公开知识", body_md="PUBLIC_ONLY"),
    )
    await service.update(actor, doc.id, KnowledgeDocUpdate(status=KnowledgeStatus.PUBLISHED))
    await service.ingest(
        actor,
        KnowledgeIngestCard(
            target_doc_id=doc.id,
            source_file_id=secret_file.id,
            points=[KnowledgePointWrite(title="secret", body_md="UNPUBLISHED_OLD0017_SECRET")],
        ),
    )
    loaded = await service.get(actor, doc.id)
    published = next(item for item in loaded.revisions if item.id == loaded.published_revision_id)
    draft = next(item for item in loaded.revisions if item.id == loaded.draft_revision_id)
    published.points_json = list(draft.points_json or [])
    published.points_review = "NEEDS_REVIEW"
    await db_session.commit()
    _login(client, "review-staff")
    listed = client.get("/api/v1/knowledge")
    assert listed.status_code == 200
    assert "UNPUBLISHED_OLD0017_SECRET" not in listed.text
    detail = client.get(f"/api/v1/knowledge/{doc.id}")
    assert detail.status_code == 200
    assert "UNPUBLISHED_OLD0017_SECRET" not in detail.text
    download = client.get(
        f"/api/v1/knowledge/{doc.id}/source-download",
        params={"file_id": str(secret_file.id)},
    )
    assert download.status_code == 403
    _login(client)
    owner_detail = client.get(f"/api/v1/knowledge/{doc.id}")
    assert owner_detail.status_code == 200
    assert "UNPUBLISHED_OLD0017_SECRET" in owner_detail.text
    reviews = {item["id"]: item["points_review"] for item in owner_detail.json()["revisions"]}
    assert reviews[str(published.id)] == "NEEDS_REVIEW"
    cleared = client.post(
        f"/api/v1/knowledge/{doc.id}/revisions/{published.id}/points-review",
        json={"action": "clear"},
        headers=_csrf(client),
    )
    assert cleared.status_code == 200
    _login(client, "review-staff")
    after = client.get(f"/api/v1/knowledge/{doc.id}")
    assert "UNPUBLISHED_OLD0017_SECRET" not in after.text


@pytest.mark.asyncio
async def test_0018_duplicate_finance_source_rows_do_not_abort_upgrade() -> None:
    with PostgresContainer("postgres:17-alpine") as postgres:
        url = postgres.get_connection_url().replace("postgresql+psycopg2", "postgresql+asyncpg")
        _migrate(url, "upgrade", "head")
        engine = create_async_engine(url)
        owner = uuid4()
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
                            :id, 'round5-owner', 'Owner',
                            '$argon2id$synthetic', now(), 'OWNER', 'ACTIVE'
                        )
                        """
                    ),
                    {"id": owner},
                )
            _migrate(url, "downgrade", "0017_round2_constraints")
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        """
                        INSERT INTO finance_import_batches(batch_key, created_by)
                        VALUES ('dup-src', :owner)
                        """
                    ),
                    {"owner": owner},
                )
                for index in (0, 1):
                    await connection.execute(
                        text(
                            """
                            INSERT INTO finance_import_rows(
                                id, batch_key, row_index, status, payload
                            )
                            VALUES (
                                :id, 'dup-src', :row_index, 'INSERTED',
                                CAST(:payload AS jsonb)
                            )
                            """
                        ),
                        {
                            "id": uuid4(),
                            "row_index": index,
                            "payload": json.dumps(
                                {"source_row": 3, "source_sheet": "Sheet1", "amount_cents": 100}
                            ),
                        },
                    )
            _migrate(url, "upgrade", "head")
            async with engine.begin() as connection:
                version = await connection.scalar(text("SELECT version_num FROM alembic_version"))
                assert version == "0021_round7_integrity"
                rows = (
                    await connection.execute(
                        text(
                            "SELECT row_index, payload FROM finance_import_rows "
                            "WHERE batch_key = 'dup-src' ORDER BY row_index"
                        )
                    )
                ).all()
                assert [row[0] for row in rows] == [0, 1]
                assert all(
                    (row[1] or {}).get("source_identity_conflict") is True
                    or (row[1] or {}).get("source_row") == 3
                    for row in rows
                )
        finally:
            await engine.dispose()
