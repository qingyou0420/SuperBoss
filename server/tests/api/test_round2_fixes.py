"""Regression tests for the second review-fix round."""

from datetime import date
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from superboss.core.actors import Actor
from superboss.core.config import Settings
from superboss.main import create_app
from superboss.modules.files.models import FileState, FolderVisibility
from superboss.modules.knowledge.router import _to_read
from superboss.modules.knowledge.schemas import (
    KnowledgeDocCreate,
    KnowledgeDocUpdate,
    KnowledgeIngestCard,
    KnowledgePointWrite,
)
from superboss.modules.knowledge.service import KnowledgeService
from superboss.modules.projects.workflow import reflow_from_anchor
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


async def _clean_files(session: AsyncSession, owner_id, count: int = 2) -> list[str]:
    folder = await add_folder(session, owner_id, visibility=FolderVisibility.ALL)
    rows = []
    for index in range(count):
        image = index % 2 == 1
        rows.append(
            make_file(
                folder_id=folder.id,
                uploader_id=owner_id,
                filename="shot.jpg" if image else "doc.pdf",
                content_type="image/jpeg" if image else "application/pdf",
                state=FileState.CLEAN,
            )
        )
    session.add_all(rows)
    await session.commit()
    return [str(item.id) for item in rows]


def test_reflow_keeps_done_dates_and_does_not_cross_them() -> None:
    nodes = [
        {
            "id": uuid4(),
            "duration_days": 3,
            "planned_start": date(2026, 9, 1),
            "planned_end": date(2026, 9, 3),
            "status": "SKIPPED",
        },
        {
            "id": uuid4(),
            "duration_days": 5,
            "planned_start": date(2026, 9, 4),
            "planned_end": date(2026, 9, 8),
            "status": "DONE",
        },
        {
            "id": uuid4(),
            "duration_days": 3,
            "planned_start": date(2026, 9, 9),
            "planned_end": date(2026, 9, 11),
            "status": "OPEN",
        },
    ]
    reflowed = reflow_from_anchor(nodes, 0, date(2026, 8, 1))
    assert reflowed[1]["planned_end"] == date(2026, 9, 8)
    assert reflowed[2]["planned_start"] == date(2026, 9, 9)


@pytest.mark.asyncio
async def test_edit_description_does_not_overwrite_contract_due(
    client: TestClient,
) -> None:
    _login(client)
    created = client.post(
        "/api/v1/projects",
        json={
            "name": "合同期限",
            "starts_on": "2026-09-01",
            "due_on": "2026-09-30",
        },
        headers=_csrf(client),
    )
    assert created.status_code == 201
    body = created.json()
    assert body["contract_due_on"] == "2026-09-30"
    assert body["due_on"] != "2026-09-30" or body["nodes"]
    patched = client.patch(
        f"/api/v1/projects/{body['id']}",
        json={"description": "只改说明"},
        headers=_csrf(client),
    )
    assert patched.status_code == 200
    assert patched.json()["contract_due_on"] == "2026-09-30"
    wrongly = client.patch(
        f"/api/v1/projects/{body['id']}",
        json={"due_on": body["due_on"], "description": "误把流程日当合同日"},
        headers=_csrf(client),
    )
    assert wrongly.status_code == 200
    assert wrongly.json()["contract_due_on"] == "2026-09-30"


@pytest.mark.asyncio
async def test_create_uses_saved_default_template(client: TestClient) -> None:
    _login(client)
    saved = client.put(
        "/api/v1/projects/workflow-templates/default",
        json={
            "name": "老板新版",
            "nodes": [
                {
                    "title": "老板定制第一阶段",
                    "duration_days": 2,
                    "required_materials": ["document"],
                },
                {
                    "title": "老板定制第二阶段",
                    "duration_days": 4,
                    "photo_required": True,
                    "required_materials": ["document", "photo"],
                },
            ],
        },
        headers=_csrf(client),
    )
    assert saved.status_code == 200
    created = client.post(
        "/api/v1/projects",
        json={"name": "用新模板", "starts_on": "2026-09-01"},
        headers=_csrf(client),
    )
    assert created.status_code == 201
    nodes = created.json()["nodes"]
    assert [item["title"] for item in nodes] == ["老板定制第一阶段", "老板定制第二阶段"]
    assert created.json()["template_version_id"]


@pytest.mark.asyncio
async def test_reschedule_rejects_skipped_anchor_and_keeps_done_order(
    client: TestClient, db_session: AsyncSession, active_owner: User
) -> None:
    _login(client)
    created = client.post(
        "/api/v1/projects",
        json={"name": "关键日期", "starts_on": "2026-09-01"},
        headers=_csrf(client),
    )
    project = created.json()
    files = await _clean_files(db_session, active_owner.id)
    first, second, third = project["nodes"][:3]
    skipped = client.post(
        f"/api/v1/projects/{project['id']}/nodes/{first['id']}/skip",
        headers=_csrf(client),
    )
    assert skipped.status_code == 200
    done = client.post(
        f"/api/v1/projects/{project['id']}/nodes/{second['id']}/complete",
        json={"evidence": [files[0]]},
        headers=_csrf(client),
    )
    assert done.status_code == 200
    blocked = client.post(
        f"/api/v1/projects/{project['id']}/schedule-from-date",
        json={"node_id": first["id"], "new_start": "2026-08-01", "reason": "提前"},
        headers=_csrf(client),
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "NODE_SKIPPED"
    moved = client.post(
        f"/api/v1/projects/{project['id']}/schedule-from-date",
        json={"node_id": third["id"], "new_start": "2026-08-09", "reason": "提前后续"},
        headers=_csrf(client),
    )
    assert moved.status_code == 409
    assert moved.json()["error"]["code"] == "SHIFT_ORDER"
    current = client.get(f"/api/v1/projects/{project['id']}").json()
    done_node = next(item for item in current["nodes"] if item["id"] == second["id"])
    later = next(item for item in current["nodes"] if item["id"] == third["id"])
    assert done_node["planned_end"] == second["planned_end"]
    assert later["planned_start"] >= done_node["planned_end"]


@pytest.mark.asyncio
async def test_complete_rejects_private_folder_file(
    client: TestClient, db_session: AsyncSession, active_owner: User
) -> None:
    _login(client)
    staff = local_user("lead-staff", display_name="Lead")
    db_session.add(staff)
    await db_session.commit()
    created = client.post(
        "/api/v1/projects",
        json={
            "name": "材料权限",
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
        filename="老板私有-利润奖金明细.pdf",
        state=FileState.CLEAN,
    )
    db_session.add(secret)
    await db_session.commit()
    client.post("/api/v1/auth/logout", headers=_csrf(client))
    _login(client, "lead-staff")
    blocked = client.post(
        f"/api/v1/projects/{project['id']}/nodes/{project['nodes'][0]['id']}/complete",
        json={"evidence": [str(secret.id)]},
        headers=_csrf(client),
    )
    assert blocked.status_code == 403


@pytest.mark.asyncio
async def test_pay_idempotency_and_amount_guard(client: TestClient) -> None:
    _login(client)
    created = client.post(
        "/api/v1/projects",
        json={"name": "付款幂等", "starts_on": "2026-09-01"},
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
            "paid_on": "2026-09-10",
            "paid_cents": 100_000,
            "idempotency_key": "pay-full-1",
        },
        headers=_csrf(client),
    )
    assert first.status_code == 200
    retry = client.post(
        f"/api/v1/finance/entries/{entry['id']}/pay",
        json={
            "paid_on": "2026-09-10",
            "paid_cents": 100_000,
            "idempotency_key": "pay-full-1",
        },
        headers=_csrf(client),
    )
    assert retry.status_code == 200
    assert retry.json()["paid_total_cents"] == 100_000
    assert len(retry.json()["payments"]) == 1
    other = client.post(
        f"/api/v1/finance/entries/{entry['id']}/pay",
        json={"paid_on": "2026-09-11", "idempotency_key": "pay-full-2"},
        headers=_csrf(client),
    )
    assert other.status_code == 409
    assert other.json()["error"]["code"] == "FINANCE_ALREADY_PAID"


@pytest.mark.asyncio
async def test_adjust_response_keeps_payments(client: TestClient) -> None:
    _login(client)
    created = client.post(
        "/api/v1/projects",
        json={"name": "调整回包", "starts_on": "2026-09-01"},
        headers=_csrf(client),
    )
    entry = client.post(
        "/api/v1/finance/entries",
        json={
            "kind": "COST",
            "scope": "PROJECT",
            "project_id": created.json()["id"],
            "amount_cents": 400_000,
            "occurred_on": "2026-09-02",
            "category": "印刷",
        },
        headers=_csrf(client),
    ).json()
    client.post(
        f"/api/v1/finance/entries/{entry['id']}/pay",
        json={"paid_on": "2026-09-10", "paid_cents": 100_000, "idempotency_key": "a"},
        headers=_csrf(client),
    )
    client.post(
        f"/api/v1/finance/entries/{entry['id']}/pay",
        json={"paid_on": "2026-09-20", "paid_cents": 200_000, "idempotency_key": "b"},
        headers=_csrf(client),
    )
    adjusted = client.post(
        f"/api/v1/finance/entries/{entry['id']}/adjustments",
        json={"field": "amount_cents", "new_value": "500000", "reason": "补记"},
        headers=_csrf(client),
    )
    assert adjusted.status_code == 200
    body = adjusted.json()
    assert body["paid_total_cents"] == 300_000
    assert body["unpaid_cents"] == 200_000
    assert len(body["payments"]) == 2


@pytest.mark.asyncio
async def test_distinct_same_fingerprint_expenses_are_not_merged(
    client: TestClient,
) -> None:
    _login(client)
    created = client.post(
        "/api/v1/projects",
        json={"name": "独立费用", "starts_on": "2026-09-01"},
        headers=_csrf(client),
    )
    project_id = created.json()["id"]
    rows = [
        {
            "kind": "COST",
            "scope": "PROJECT",
            "project_id": project_id,
            "amount_cents": 27000,
            "occurred_on": "2026-09-02",
            "category": "外包",
            "memo": "甲供应商第一笔",
            "source_row": 2,
        },
        {
            "kind": "COST",
            "scope": "PROJECT",
            "project_id": project_id,
            "amount_cents": 27000,
            "occurred_on": "2026-09-02",
            "category": "外包",
            "memo": "乙供应商独立第二笔",
            "voucher": "receipt-b.png",
            "source_row": 3,
        },
    ]
    imported = client.post(
        "/api/v1/finance/import",
        json={"batch_key": "two-vendors", "rows": rows},
        headers=_csrf(client),
    )
    assert imported.status_code == 200
    assert imported.json()["inserted"] == 2
    assert imported.json()["unresolved"] == []
    listed = client.get("/api/v1/finance/entries", params={"month": "2026-09"}).json()
    memos = {item["memo"] for item in listed if item["amount_cents"] == 27000}
    assert memos == {"甲供应商第一笔", "乙供应商独立第二笔"}


@pytest.mark.asyncio
async def test_import_keeps_excel_row_and_entry_link(client: TestClient) -> None:
    _login(client)
    created = client.post(
        "/api/v1/projects",
        json={"name": "行号追溯", "starts_on": "2026-09-01"},
        headers=_csrf(client),
    )
    imported = client.post(
        "/api/v1/finance/import",
        json={
            "batch_key": "row-trace",
            "rows": [
                {
                    "kind": "COST",
                    "scope": "PROJECT",
                    "project_id": created.json()["id"],
                    "amount_cents": 10000,
                    "occurred_on": "2026-09-02",
                    "category": "印刷",
                    "source_row": 8,
                },
                {
                    "kind": "COST",
                    "scope": "PROJECT",
                    "project_name": "没有这个项目",
                    "amount_cents": 20000,
                    "occurred_on": "2026-09-03",
                    "category": "印刷",
                    "source_row": 9,
                },
            ],
        },
        headers=_csrf(client),
    )
    body = imported.json()
    assert body["inserted"] == 1
    assert body["unresolved"][0]["row"] == 9
    assert body["entries"][0]["id"]
    assert UUID(body["entries"][0]["id"])


@pytest.mark.asyncio
async def test_directory_same_identity_scale_mismatch_stays_separate(
    client: TestClient,
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
    payload = _xlsx(
        headers,
        [
            ["同名花园", "芗城区", "东铺头街道", "A社区", "甲物业", "100", "10000", "13800001111"],
            ["同名花园", "芗城区", "东铺头街道", "A社区", "甲物业", "180", "18000", "13900002222"],
        ],
    )
    imported = client.post(
        "/api/v1/directory/imports",
        files={
            "file": (
                "芗城同名.xlsx",
                payload,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
        headers=_csrf(client),
    )
    assert imported.status_code == 200
    body = imported.json()
    assert body["inserted"] == 2
    assert body["conflicts"]
    assert body["conflicts"][0]["reason"] == "IDENTITY_SCALE_MISMATCH"
    listed = client.get("/api/v1/directory").json()
    assert listed["total"] == 2
    pending = client.get("/api/v1/directory/conflicts")
    assert pending.status_code == 200
    body = pending.json()
    assert body["total"] >= 1
    assert body["items"]


@pytest.mark.asyncio
async def test_knowledge_ingest_snapshots_and_hides_unpublished_points(
    db_session: AsyncSession, active_owner: User
) -> None:
    actor = Actor(subject_id=active_owner.id, role=Role.OWNER)
    service = KnowledgeService(db_session)
    created = await service.create(
        actor,
        KnowledgeDocCreate(title="公示要点", body_md="公开正文", change_reason="初稿"),
    )
    await service.update(actor, created.id, KnowledgeDocUpdate(status="PUBLISHED"))
    ingested = await service.ingest(
        actor,
        KnowledgeIngestCard(
            target_doc_id=created.id,
            points=[KnowledgePointWrite(title="草稿点", body_md="UNPUBLISHED_SECRET")],
        ),
    )
    assert ingested.draft_revision_id != ingested.published_revision_id
    assert any("UNPUBLISHED_SECRET" in item.body_md for item in ingested.points)
    staff = Actor(subject_id=active_owner.id, role=Role.STAFF)
    visible = _to_read(staff, await service.get(staff, created.id))
    assert visible.body_md == "公开正文"
    assert all("UNPUBLISHED_SECRET" not in item.body_md for item in visible.points)
    hits = await service.search(staff, "UNPUBLISHED_SECRET")
    assert hits == [] or all("UNPUBLISHED_SECRET" not in item.get("body", "") for item in hits)


@pytest.mark.asyncio
async def test_fresh_session_ingest_creates_revision(
    db_session: AsyncSession, active_owner: User
) -> None:
    actor = Actor(subject_id=active_owner.id, role=Role.OWNER)
    service = KnowledgeService(db_session)
    doc = await service.ingest(
        actor,
        KnowledgeIngestCard(
            new_doc_title="新导入",
            points=[KnowledgePointWrite(title="点", body_md="导入正文")],
        ),
    )
    assert doc.draft_revision_id is not None
    assert len(doc.revisions) == 1
    assert doc.revisions[0].points_json
