"""Regression tests for the third review-fix round."""

from datetime import date
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from superboss.core.actors import Actor
from superboss.core.config import Settings
from superboss.core.errors import ConflictError
from superboss.main import create_app
from superboss.modules.files.models import FileState, FolderVisibility
from superboss.modules.finance.models import FinanceEntry
from superboss.modules.finance.schemas import FinancePayCommand
from superboss.modules.finance.service import FinanceService
from superboss.modules.knowledge.router import _to_read
from superboss.modules.knowledge.schemas import (
    KnowledgeDocCreate,
    KnowledgeDocUpdate,
    KnowledgeIngestCard,
    KnowledgePointWrite,
)
from superboss.modules.knowledge.service import KnowledgeService
from superboss.modules.users.models import Role, User
from tests.directory.test_directory import _xlsx
from tests.files.factory import add_folder, make_file
from tests.files.storage import InMemoryObjectStorage
from tests.identity import LOCAL_TEST_PASSWORD, local_user


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


def _login(client: TestClient, username: str = "owner") -> None:
    assert client.get("/api/v1/auth/csrf").status_code == 204
    assert (
        client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": LOCAL_TEST_PASSWORD},
            headers={"X-CSRF-Token": str(client.cookies.get("XSRF-TOKEN"))},
        ).status_code
        == 204
    )


def _csrf(client: TestClient) -> dict[str, str]:
    return {"X-CSRF-Token": str(client.cookies.get("XSRF-TOKEN"))}


@pytest.mark.asyncio
async def test_staff_project_response_redacts_private_evidence(
    client: TestClient, db_session: AsyncSession, active_owner: User
) -> None:
    _login(client)
    staff = local_user("evidence-staff", display_name="Lead")
    db_session.add(staff)
    await db_session.commit()
    created = client.post(
        "/api/v1/projects",
        json={
            "name": "私有材料项目",
            "starts_on": "2026-09-01",
            "lead_user_id": str(staff.id),
        },
        headers=_csrf(client),
    )
    project = created.json()
    private = await add_folder(
        db_session, active_owner.id, name="老板私有", visibility=FolderVisibility.OWNER_ONLY
    )
    secret = make_file(
        folder_id=private.id,
        uploader_id=active_owner.id,
        filename="synthetic-private-profit-50000.pdf",
        state=FileState.CLEAN,
        project_id=UUID(project["id"]),
    )
    db_session.add(secret)
    await db_session.commit()
    completed = client.post(
        f"/api/v1/projects/{project['id']}/nodes/{project['nodes'][0]['id']}/complete",
        json={"evidence": [str(secret.id)]},
        headers=_csrf(client),
    )
    assert completed.status_code == 200
    owner_names = [
        item.get("filename")
        for node in completed.json()["nodes"]
        for item in node.get("evidence") or []
        if isinstance(item, dict)
    ]
    assert "synthetic-private-profit-50000.pdf" in owner_names
    client.post("/api/v1/auth/logout", headers=_csrf(client))
    _login(client, "evidence-staff")
    staff_view = client.get(f"/api/v1/projects/{project['id']}")
    assert staff_view.status_code == 200
    leaked = [
        item
        for node in staff_view.json()["nodes"]
        for item in node.get("evidence") or []
        if isinstance(item, dict)
        and (
            item.get("filename") == "synthetic-private-profit-50000.pdf"
            or item.get("file_id") == str(secret.id)
        )
    ]
    assert leaked == []
    listed = client.get("/api/v1/projects").json()
    listed_leaked = [
        item
        for project_row in listed
        for node in project_row.get("nodes") or []
        for item in node.get("evidence") or []
        if isinstance(item, dict)
        and (
            item.get("filename") == "synthetic-private-profit-50000.pdf"
            or item.get("file_id") == str(secret.id)
        )
    ]
    assert listed_leaked == []


@pytest.mark.asyncio
async def test_import_recovery_uses_source_row_not_filtered_index(client: TestClient) -> None:
    _login(client)
    created = client.post(
        "/api/v1/projects",
        json={"name": "解析恢复", "starts_on": "2026-09-01"},
        headers=_csrf(client),
    )
    project_id = created.json()["id"]
    first = client.post(
        "/api/v1/finance/import",
        json={
            "batch_key": "parse-recovery",
            "rows": [
                {
                    "kind": "COST",
                    "scope": "PROJECT",
                    "project_id": project_id,
                    "amount_cents": 1200,
                    "occurred_on": "2026-09-03",
                    "category": "印刷",
                    "source_row": 3,
                }
            ],
        },
        headers=_csrf(client),
    )
    assert first.status_code == 200
    assert first.json()["inserted"] == 1
    retry = client.post(
        "/api/v1/finance/import",
        json={
            "batch_key": "parse-recovery",
            "rows": [
                {
                    "kind": "COST",
                    "scope": "PROJECT",
                    "project_id": project_id,
                    "amount_cents": 1100,
                    "occurred_on": "2026-09-02",
                    "category": "印刷",
                    "source_row": 2,
                },
                {
                    "kind": "COST",
                    "scope": "PROJECT",
                    "project_id": project_id,
                    "amount_cents": 1200,
                    "occurred_on": "2026-09-03",
                    "category": "印刷",
                    "source_row": 3,
                },
            ],
        },
        headers=_csrf(client),
    )
    body = retry.json()
    assert body["inserted"] == 1
    amounts = {
        item["amount_cents"]
        for item in client.get("/api/v1/finance/entries", params={"month": "2026-09"}).json()
        if item["project_id"] == project_id
    }
    assert amounts == {1100, 1200}
    rows = client.get("/api/v1/finance/import-rows", params={"status": "INSERTED"}).json()
    indexes = {item["row_index"] for item in rows["items"] if item["batch_key"] == "parse-recovery"}
    assert indexes == {2, 3}


@pytest.mark.asyncio
async def test_duplicate_candidate_can_insert_independent_or_link(
    client: TestClient,
) -> None:
    _login(client)
    created = client.post(
        "/api/v1/projects",
        json={"name": "候选核对", "starts_on": "2026-09-01"},
        headers=_csrf(client),
    )
    project_id = created.json()["id"]
    first = client.post(
        "/api/v1/finance/import",
        json={
            "batch_key": "candidate-loop",
            "rows": [
                {
                    "kind": "COST",
                    "scope": "PROJECT",
                    "project_id": project_id,
                    "amount_cents": 25000,
                    "occurred_on": "2026-09-02",
                    "category": "外包",
                    "memo": "同一备注",
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
            "batch_key": "candidate-loop-2",
            "rows": [
                {
                    "kind": "COST",
                    "scope": "PROJECT",
                    "project_id": project_id,
                    "amount_cents": 25000,
                    "occurred_on": "2026-09-02",
                    "category": "外包",
                    "memo": "同一备注",
                    "voucher": "receipt-a.png",
                    "source_row": 5,
                }
            ],
        },
        headers=_csrf(client),
    )
    unresolved = second.json()["unresolved"]
    assert unresolved[0]["reason"] == "DUPLICATE_CANDIDATE"
    assert unresolved[0]["candidate_entry_id"]
    pending = client.get("/api/v1/finance/import-rows").json()
    assert pending["total"] >= 1
    linked = client.post(
        "/api/v1/finance/imports/candidate-loop-2/rows/5/resolve",
        json={"action": "link_voucher", "entry_id": unresolved[0]["candidate_entry_id"]},
        headers=_csrf(client),
    )
    assert linked.status_code == 200
    assert linked.json()["status"] == "LINKED"
    listed = client.get("/api/v1/finance/entries", params={"month": "2026-09"}).json()
    match = next(item for item in listed if item["amount_cents"] == 25000)
    assert match["voucher"] == "receipt-a.png"


@pytest.mark.asyncio
async def test_mark_paid_refreshes_preloaded_payments(
    postgres_database: str, client: TestClient
) -> None:
    _login(client)
    created = client.post(
        "/api/v1/projects",
        json={"name": "缓存刷新", "starts_on": "2026-09-01"},
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
    engine = create_async_engine(postgres_database)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    actor = Actor(subject_id=UUID(entry["id"]), role=Role.OWNER)
    try:
        async with factory() as session_a, factory() as session_b:
            owner_id = await session_a.scalar(
                select(FinanceEntry.created_by).where(FinanceEntry.id == UUID(entry["id"]))
            )
            actor = Actor(subject_id=owner_id, role=Role.OWNER)
            loaded_a = await session_a.scalar(
                select(FinanceEntry)
                .where(FinanceEntry.id == UUID(entry["id"]))
                .options(
                    selectinload(FinanceEntry.adjustments), selectinload(FinanceEntry.payments)
                )
            )
            loaded_b = await session_b.scalar(
                select(FinanceEntry)
                .where(FinanceEntry.id == UUID(entry["id"]))
                .options(
                    selectinload(FinanceEntry.adjustments), selectinload(FinanceEntry.payments)
                )
            )
            assert loaded_a is not None and loaded_b is not None
            first = await FinanceService(session_a).mark_paid(
                actor,
                UUID(entry["id"]),
                FinancePayCommand(
                    paid_on=date(2026, 9, 10), paid_cents=100_000, idempotency_key="k1"
                ),
            )
            await session_a.commit()
            assert first.paid_total_cents == 100_000
            with pytest.raises(ConflictError) as error:
                await FinanceService(session_b).mark_paid(
                    actor,
                    UUID(entry["id"]),
                    FinancePayCommand(
                        paid_on=date(2026, 9, 10), paid_cents=100_000, idempotency_key="k2"
                    ),
                )
            assert error.value.code == "FINANCE_ALREADY_PAID"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_directory_cross_batch_does_not_merge_or_fill(
    client: TestClient, db_session: AsyncSession
) -> None:
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
                "batch-a.xlsx",
                _xlsx(
                    headers,
                    [["合成花园", "芗城区", "东铺头街道", "A社区", "", "100", "1000", ""]],
                ),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
        headers=_csrf(client),
    )
    assert first.json()["inserted"] == 1
    second = client.post(
        "/api/v1/directory/imports",
        files={
            "file": (
                "batch-b.xlsx",
                _xlsx(
                    headers,
                    [["合成花园", "芗城区", "东铺头街道", "A社区", "新物业", "300", "3000", ""]],
                ),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
        headers=_csrf(client),
    )
    body = second.json()
    assert body["inserted"] == 0
    assert body["updated"] == 0
    assert body["conflicts"]
    listed = client.get("/api/v1/directory").json()
    assert listed["total"] == 1
    assert listed["items"][0]["households"] == 100
    assert listed["items"][0]["property_company"] == ""
    pending = client.get("/api/v1/directory/conflicts").json()
    assert pending["total"] >= 1
    conflict_id = pending["items"][0]["id"]
    split = client.post(
        f"/api/v1/directory/conflicts/{conflict_id}/resolve",
        json={"action": "split"},
        headers=_csrf(client),
    )
    assert split.status_code == 200
    assert split.json()["status"] == "SPLIT"
    listed = client.get("/api/v1/directory").json()
    assert listed["total"] == 2


@pytest.mark.asyncio
async def test_unpublished_historical_revision_is_hidden_from_staff(
    db_session: AsyncSession, active_owner: User
) -> None:
    actor = Actor(subject_id=active_owner.id, role=Role.OWNER)
    service = KnowledgeService(db_session)
    created = await service.create(
        actor,
        KnowledgeDocCreate(title="版本授权", body_md="PUBLIC_SEARCH_TOKEN", change_reason="v1"),
    )
    await service.update(actor, created.id, KnowledgeDocUpdate(status="PUBLISHED"))
    await service.update(
        actor,
        created.id,
        KnowledgeDocUpdate(body_md="NEVER_PUBLISHED_VERSION_TWO_SECRET", change_reason="v2"),
    )
    await service.update(
        actor,
        created.id,
        KnowledgeDocUpdate(body_md="public v3", change_reason="v3"),
    )
    await service.update(actor, created.id, KnowledgeDocUpdate(status="PUBLISHED"))
    await service.update(
        actor,
        created.id,
        KnowledgeDocUpdate(body_md="DRAFT_SEARCH_TOKEN", change_reason="draft-only"),
    )
    staff = Actor(subject_id=active_owner.id, role=Role.STAFF)
    visible = _to_read(staff, await service.get(staff, created.id))
    bodies = [item.body_md for item in visible.revisions]
    assert "NEVER_PUBLISHED_VERSION_TWO_SECRET" not in bodies
    assert "DRAFT_SEARCH_TOKEN" not in bodies
    assert visible.body_md == "public v3"
    owner_view = _to_read(actor, await service.get(actor, created.id))
    assert any(
        "NEVER_PUBLISHED_VERSION_TWO_SECRET" in item.body_md for item in owner_view.revisions
    )
    private_hits = await service.search(staff, "DRAFT_SEARCH_TOKEN")
    secret_hits = await service.search(staff, "NEVER_PUBLISHED_VERSION_TWO_SECRET")
    public_hits = await service.search(staff, "public v3")
    assert private_hits == []
    assert secret_hits == []
    assert any(item["kind"] == "doc" for item in public_hits)


@pytest.mark.asyncio
async def test_points_json_omitted_insert_succeeds(
    db_session: AsyncSession, active_owner: User
) -> None:
    actor = Actor(subject_id=active_owner.id, role=Role.OWNER)
    service = KnowledgeService(db_session)
    doc = await service.create(actor, KnowledgeDocCreate(title="默认值", body_md="正文"))
    revision_id = uuid4()
    await db_session.execute(
        text(
            """
            INSERT INTO knowledge_revisions (id, doc_id, version, created_by)
            VALUES (:id, :doc_id, 9, :created_by)
            """
        ),
        {"id": revision_id, "doc_id": doc.id, "created_by": active_owner.id},
    )
    await db_session.flush()
    stored = await db_session.scalar(
        text("SELECT points_json FROM knowledge_revisions WHERE id = :id"),
        {"id": revision_id},
    )
    assert stored == [] or stored == "[]"


@pytest.mark.asyncio
async def test_knowledge_ingest_keeps_unpublished_search_hidden(
    db_session: AsyncSession, active_owner: User
) -> None:
    actor = Actor(subject_id=active_owner.id, role=Role.OWNER)
    service = KnowledgeService(db_session)
    created = await service.create(
        actor,
        KnowledgeDocCreate(title="公示要点", body_md="公开正文", change_reason="初稿"),
    )
    await service.update(actor, created.id, KnowledgeDocUpdate(status="PUBLISHED"))
    await service.ingest(
        actor,
        KnowledgeIngestCard(
            target_doc_id=created.id,
            points=[KnowledgePointWrite(title="草稿点", body_md="UNPUBLISHED_SECRET")],
        ),
    )
    staff = Actor(subject_id=active_owner.id, role=Role.STAFF)
    hits = await service.search(staff, "UNPUBLISHED_SECRET")
    assert hits == []
