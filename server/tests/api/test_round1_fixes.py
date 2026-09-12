"""Regression tests for the first review-fix round."""

from datetime import date

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from superboss.core.actors import Actor
from superboss.core.config import Settings
from superboss.main import create_app
from superboss.modules.directory.service import map_row
from superboss.modules.files.models import FileState, FolderVisibility
from superboss.modules.knowledge.schemas import KnowledgeDocCreate, KnowledgeDocUpdate
from superboss.modules.knowledge.service import KnowledgeService
from superboss.modules.projects.models import Project, ProjectStatus
from superboss.modules.users.models import Role, User
from tests.files.factory import add_folder, make_file
from tests.files.storage import InMemoryObjectStorage
from tests.identity import LOCAL_TEST_PASSWORD


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


@pytest.mark.asyncio
async def test_get_does_not_invent_nodes_or_rewrite_due_on(
    client: TestClient, db_session: AsyncSession
) -> None:
    _login(client)
    project = Project(
        name="历史归档项目",
        starts_on=date(2026, 9, 1),
        due_on=date(2026, 9, 30),
        progress_percent=100,
        status=ProjectStatus.ARCHIVED,
    )
    db_session.add(project)
    await db_session.commit()
    loaded = client.get(f"/api/v1/projects/{project.id}")
    assert loaded.status_code == 200
    body = loaded.json()
    assert body["nodes"] == []
    assert body["workflow_pending"] is True
    assert body["due_on"] == "2026-09-30"


@pytest.mark.asyncio
async def test_complete_node_rejects_fake_filenames_and_requires_real_files(
    client: TestClient, db_session: AsyncSession, active_owner: User
) -> None:
    _login(client)
    created = client.post(
        "/api/v1/projects",
        json={"name": "材料门槛", "starts_on": "2026-09-01"},
        headers=_csrf(client),
    )
    assert created.status_code == 201
    project = created.json()
    first = project["nodes"][0]
    fake = client.post(
        f"/api/v1/projects/{project['id']}/nodes/{first['id']}/complete",
        json={"evidence": ["not-an-upload"]},
        headers=_csrf(client),
    )
    assert fake.status_code == 409
    assert fake.json()["error"]["code"] == "NODE_MATERIALS_INVALID"
    missing = client.post(
        f"/api/v1/projects/{project['id']}/nodes/{first['id']}/complete",
        json={"evidence": []},
        headers=_csrf(client),
    )
    assert missing.status_code == 409
    assert missing.json()["error"]["code"] == "NODE_MATERIALS_REQUIRED"
    files = await _clean_files(db_session, active_owner.id)
    done = client.post(
        f"/api/v1/projects/{project['id']}/nodes/{first['id']}/complete",
        json={"evidence": [files[0]]},
        headers=_csrf(client),
    )
    assert done.status_code == 200
    assert done.json()["nodes"][0]["status"] == "DONE"
    second = project["nodes"][1]
    second_done = client.post(
        f"/api/v1/projects/{project['id']}/nodes/{second['id']}/complete",
        json={"evidence": [files[0]]},
        headers=_csrf(client),
    )
    assert second_done.status_code == 200
    photo = next(item for item in project["nodes"] if item["photo_required"] is True)
    blocked = client.post(
        f"/api/v1/projects/{project['id']}/nodes/{photo['id']}/complete",
        json={"evidence": [files[0]]},
        headers=_csrf(client),
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "NODE_MATERIALS_REQUIRED"
    ok = client.post(
        f"/api/v1/projects/{project['id']}/nodes/{photo['id']}/complete",
        json={"evidence": [files[0], files[1]]},
        headers=_csrf(client),
    )
    assert ok.status_code == 200
    assert ok.json()["nodes"][2]["status"] == "DONE"


@pytest.mark.asyncio
async def test_shift_cannot_cross_completed_predecessor(
    client: TestClient, db_session: AsyncSession, active_owner: User
) -> None:
    _login(client)
    created = client.post(
        "/api/v1/projects",
        json={"name": "调期顺序", "starts_on": "2026-09-01"},
        headers=_csrf(client),
    )
    project = created.json()
    files = await _clean_files(db_session, active_owner.id)
    for node in project["nodes"][:3]:
        evidence = [files[0], files[1]] if node["photo_required"] else [files[0]]
        assert (
            client.post(
                f"/api/v1/projects/{project['id']}/nodes/{node['id']}/complete",
                json={"evidence": evidence},
                headers=_csrf(client),
            ).status_code
            == 200
        )
    target = project["nodes"][3]
    preview = client.post(
        f"/api/v1/projects/{project['id']}/schedule-preview",
        json={"node_id": target["id"], "days": -30},
        headers=_csrf(client),
    )
    assert preview.status_code == 200
    assert preview.json()["valid"] is False
    shifted = client.post(
        f"/api/v1/projects/{project['id']}/schedule",
        json={"node_id": target["id"], "days": -30, "reason": "提前"},
        headers=_csrf(client),
    )
    assert shifted.status_code == 409
    assert shifted.json()["error"]["code"] == "SHIFT_ORDER"
    blank = client.post(
        f"/api/v1/projects/{project['id']}/schedule",
        json={"node_id": target["id"], "days": 5, "reason": "  "},
        headers=_csrf(client),
    )
    assert blank.status_code == 422


@pytest.mark.asyncio
async def test_payments_accumulate_and_unpaid_uses_actual_cash(
    client: TestClient, db_session: AsyncSession
) -> None:
    _login(client)
    created = client.post(
        "/api/v1/projects",
        json={"name": "付款口径", "starts_on": "2026-09-01", "service_fee_cents": 3_000_000},
        headers=_csrf(client),
    )
    project_id = created.json()["id"]
    entry = client.post(
        "/api/v1/finance/entries",
        json={
            "kind": "COST",
            "scope": "PROJECT",
            "project_id": project_id,
            "amount_cents": 400_000,
            "occurred_on": "2026-09-02",
            "category": "印刷",
        },
        headers=_csrf(client),
    )
    assert entry.status_code == 201
    unpaid = client.get("/api/v1/finance/overview")
    assert unpaid.json()["paid_cents"] == 0
    assert unpaid.json()["unpaid_cents"] == 400_000
    first = client.post(
        f"/api/v1/finance/entries/{entry.json()['id']}/pay",
        json={"paid_on": "2026-09-10", "paid_cents": 100_000},
        headers=_csrf(client),
    )
    assert first.status_code == 200
    assert first.json()["paid_total_cents"] == 100_000
    assert first.json()["unpaid_cents"] == 300_000
    second = client.post(
        f"/api/v1/finance/entries/{entry.json()['id']}/pay",
        json={"paid_on": "2026-10-10", "paid_cents": 200_000},
        headers=_csrf(client),
    )
    assert second.status_code == 200
    assert second.json()["paid_total_cents"] == 300_000
    assert len(second.json()["payments"]) == 2
    overview = client.get("/api/v1/finance/overview")
    assert overview.json()["paid_cents"] == 300_000
    assert overview.json()["unpaid_cents"] == 100_000


@pytest.mark.asyncio
async def test_gross_is_sum_of_per_project_rewards(
    client: TestClient, db_session: AsyncSession
) -> None:
    _login(client)
    first = client.post(
        "/api/v1/projects",
        json={"name": "毛利甲", "starts_on": "2026-09-01", "service_fee_cents": 3_000_000},
        headers=_csrf(client),
    )
    second = client.post(
        "/api/v1/projects",
        json={"name": "毛利乙", "starts_on": "2026-09-01", "service_fee_cents": 3_000_000},
        headers=_csrf(client),
    )
    from uuid import UUID

    from sqlalchemy import update

    for project, cost in ((first.json(), 400_000), (second.json(), 1_000_000)):
        client.post(
            "/api/v1/finance/entries",
            json={
                "kind": "COST",
                "scope": "PROJECT",
                "project_id": project["id"],
                "amount_cents": cost,
                "occurred_on": "2026-09-02",
                "category": "外包",
            },
            headers=_csrf(client),
        )
    await db_session.execute(
        update(Project)
        .where(Project.id.in_([UUID(first.json()["id"]), UUID(second.json()["id"])]))
        .values(service_completed_on=date(2026, 9, 20))
    )
    await db_session.commit()
    overview = client.get("/api/v1/finance/overview")
    assert overview.json()["completed_gross_cents"] == 4_100_000


@pytest.mark.asyncio
async def test_voucher_does_not_duplicate_fingerprint(client: TestClient) -> None:
    _login(client)
    created = client.post(
        "/api/v1/projects",
        json={"name": "凭证去重", "starts_on": "2026-09-01"},
        headers=_csrf(client),
    )
    project_id = created.json()["id"]
    row = {
        "kind": "COST",
        "scope": "PROJECT",
        "project_id": project_id,
        "amount_cents": 400_000,
        "occurred_on": "2026-09-02",
        "category": "印刷",
        "memo": "印刷费",
    }
    first = client.post(
        "/api/v1/finance/import",
        json={"batch_key": "batch-a", "rows": [row]},
        headers=_csrf(client),
    )
    assert first.json()["inserted"] == 1
    second = client.post(
        "/api/v1/finance/import",
        json={
            "batch_key": "batch-b",
            "rows": [{**row, "voucher": "receipt-1.png"}],
        },
        headers=_csrf(client),
    )
    assert second.json()["inserted"] == 0
    assert second.json()["unresolved"][0]["reason"] == "DUPLICATE_CANDIDATE"
    listed = client.get("/api/v1/finance/entries", params={"month": "2026-09"})
    printing = [item for item in listed.json() if item["category"] == "印刷"]
    assert len(printing) == 1
    assert printing[0]["voucher"] == ""


@pytest.mark.asyncio
async def test_partial_batch_can_retry_unresolved_rows(client: TestClient) -> None:
    _login(client)
    created = client.post(
        "/api/v1/projects",
        json={"name": "已有项目", "starts_on": "2026-09-01"},
        headers=_csrf(client),
    )
    row_ok = {
        "kind": "COST",
        "scope": "PROJECT",
        "project_id": created.json()["id"],
        "amount_cents": 10000,
        "occurred_on": "2026-09-02",
        "category": "印刷",
    }
    row_missing = {
        "kind": "COST",
        "scope": "PROJECT",
        "project_name": "尚未建立",
        "amount_cents": 20000,
        "occurred_on": "2026-09-03",
        "category": "印刷",
    }
    first = client.post(
        "/api/v1/finance/import",
        json={"batch_key": "partial-batch", "rows": [row_ok, row_missing]},
        headers=_csrf(client),
    )
    assert first.json()["inserted"] == 1
    assert first.json()["unresolved"][0]["reason"] == "PROJECT_UNRESOLVED"
    client.post(
        "/api/v1/projects",
        json={"name": "尚未建立", "starts_on": "2026-09-01"},
        headers=_csrf(client),
    )
    second = client.post(
        "/api/v1/finance/import",
        json={"batch_key": "partial-batch", "rows": [row_ok, row_missing]},
        headers=_csrf(client),
    )
    assert second.json()["replayed"] is True
    assert second.json()["inserted"] == 1
    assert second.json()["unresolved"] == []


@pytest.mark.asyncio
async def test_directory_row_collision_creates_new_identity(
    client: TestClient, db_session: AsyncSession
) -> None:
    from tests.directory.test_directory import _xlsx

    _login(client)
    headers = [
        "物业小区名称",
        "县区",
        "小区所在街道 （乡镇）",
        "小区所在社区（村）",
        "物业企业全称",
        "总户数（户）",
        "联系手机",
    ]
    first = _xlsx(
        headers,
        [["甲小区", "芗城区", "东铺头街道", "A社区", "", "100", "13800001111"]],
    )
    assert (
        client.post(
            "/api/v1/directory/imports",
            files={"file": ("名录.xlsx", first)},
            headers=_csrf(client),
        ).json()["inserted"]
        == 1
    )
    second = _xlsx(
        headers,
        [["乙小区", "芗城区", "西桥街道", "B社区", "乙物业", "200", "13900002222"]],
    )
    imported = client.post(
        "/api/v1/directory/imports",
        files={"file": ("名录.xlsx", second)},
        headers=_csrf(client),
    )
    body = imported.json()
    assert body["inserted"] == 1
    listed = client.get("/api/v1/directory")
    names = {item["name"] for item in listed.json()["items"]}
    assert names == {"甲小区", "乙小区"}
    jia = next(item for item in listed.json()["items"] if item["name"] == "甲小区")
    assert jia["property_company"] == ""


@pytest.mark.asyncio
async def test_knowledge_keeps_published_revision(
    db_session: AsyncSession, active_owner: User
) -> None:
    actor = Actor(subject_id=active_owner.id, role=Role.OWNER)
    service = KnowledgeService(db_session)
    doc = await service.create(
        actor,
        KnowledgeDocCreate(
            title="名单公示",
            body_md="第一版正文",
            change_reason="初稿",
            stage_title="候选人名单公示",
        ),
    )
    published = await service.update(actor, doc.id, KnowledgeDocUpdate(status="PUBLISHED"))
    assert published.status.value == "PUBLISHED"
    edited = await service.update(
        actor,
        doc.id,
        KnowledgeDocUpdate(body_md="第二版正文", change_reason="社区要求补充照片"),
    )
    assert edited.body_md == "第二版正文"
    assert len(edited.revisions) >= 2
    from superboss.modules.knowledge.router import _to_read

    staff = Actor(subject_id=active_owner.id, role=Role.STAFF)
    visible = _to_read(staff, await service.get(staff, doc.id))
    assert visible.body_md == "第一版正文"


@pytest.mark.asyncio
async def test_paid_rewards_leave_pending_zero(
    client: TestClient, db_session: AsyncSession
) -> None:
    from uuid import UUID

    from sqlalchemy import update

    _login(client)
    created = client.post(
        "/api/v1/projects",
        json={"name": "已发奖励", "starts_on": "2026-09-01", "service_fee_cents": 3_000_000},
        headers=_csrf(client),
    )
    project_id = created.json()["id"]
    client.post(
        "/api/v1/finance/entries",
        json={
            "kind": "INCOME",
            "scope": "PROJECT",
            "project_id": project_id,
            "amount_cents": 3_000_000,
            "occurred_on": "2026-09-10",
            "category": "尾款",
        },
        headers=_csrf(client),
    )
    client.post(
        "/api/v1/finance/entries",
        json={
            "kind": "COST",
            "scope": "PROJECT",
            "project_id": project_id,
            "amount_cents": 400_000,
            "occurred_on": "2026-09-02",
            "category": "印刷",
        },
        headers=_csrf(client),
    )
    surplus = client.post(
        "/api/v1/finance/entries",
        json={
            "kind": "COST",
            "scope": "PROJECT",
            "project_id": project_id,
            "amount_cents": 200_000,
            "occurred_on": "2026-09-20",
            "category": "节余奖金",
        },
        headers=_csrf(client),
    )
    pool = client.post(
        "/api/v1/finance/entries",
        json={
            "kind": "COST",
            "scope": "PROJECT",
            "project_id": project_id,
            "amount_cents": 300_000,
            "occurred_on": "2026-09-20",
            "category": "执行部抽成",
        },
        headers=_csrf(client),
    )
    client.post(
        f"/api/v1/finance/entries/{surplus.json()['id']}/pay",
        json={"paid_on": "2026-09-20"},
        headers=_csrf(client),
    )
    client.post(
        f"/api/v1/finance/entries/{pool.json()['id']}/pay",
        json={"paid_on": "2026-09-20"},
        headers=_csrf(client),
    )
    await db_session.execute(
        update(Project)
        .where(Project.id == UUID(project_id))
        .values(service_completed_on=date(2026, 9, 20))
    )
    await db_session.commit()
    overview = client.get("/api/v1/finance/overview")
    pending = overview.json()["pending_rewards"]
    assert pending == []


def test_floor_area_header_normalizes() -> None:
    mapped = map_row(
        {
            "_row": 3,
            "物业小区名称": "测试小区",
            "总建筑面积 （平方）": "12345",
            "总户数（户）": "180户",
        },
        "芗城.xlsx",
        "芗城区",
    )
    assert mapped["floor_area"] == "12345"
    assert mapped["households"] == 180
