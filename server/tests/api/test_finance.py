"""Finance visibility, write gates, and adjustment behavior."""

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from superboss.core.config import Settings
from superboss.main import create_app
from superboss.modules.projects.models import Project
from superboss.modules.users.models import Role, User
from tests.files.storage import InMemoryObjectStorage
from tests.identity import LOCAL_TEST_PASSWORD, local_user


@pytest_asyncio.fixture
async def finance_client(db_session: AsyncSession, test_settings: Settings, active_owner: User):
    del active_owner
    await db_session.commit()
    app = create_app(
        test_settings,
        object_storage=InMemoryObjectStorage(),
        enqueue_file_scan=lambda _file_id, _delivery_key: None,
    )
    with TestClient(app, base_url="https://testserver") as client:
        yield client


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
async def test_owner_creates_company_and_project_costs(
    finance_client, db_session: AsyncSession
) -> None:
    client = finance_client
    project = Project(name="星野合作")
    db_session.add(project)
    await db_session.commit()
    _login(client)
    company = client.post(
        "/api/v1/finance/entries",
        json={
            "kind": "COST",
            "scope": "COMPANY",
            "amount_cents": 800_000,
            "occurred_on": "2026-09-01",
            "category": "房租",
        },
        headers=_csrf(client),
    )
    project_cost = client.post(
        "/api/v1/finance/entries",
        json={
            "kind": "COST",
            "scope": "PROJECT",
            "project_id": str(project.id),
            "amount_cents": 1_200_000,
            "occurred_on": "2026-09-02",
            "category": "外包",
        },
        headers=_csrf(client),
    )
    assert company.status_code == 201 and company.json()["visibility"] == "MANAGEMENT"
    assert project_cost.status_code == 201 and project_cost.json()["visibility"] == "ALL"
    summary = client.get("/api/v1/finance/summary", params={"month": "2026-09"})
    assert summary.status_code == 200
    body = summary.json()
    assert body["company"]["cost_cents"] == 800_000
    assert body["projects"][0]["cost_cents"] == 1_200_000
    assert body["projects"][0]["income_cents"] == 0


@pytest.mark.asyncio
async def test_import_batch_is_idempotent_and_excludes_reward_from_cost(
    finance_client, db_session: AsyncSession
) -> None:
    client = finance_client
    project = Project(name="云栖里大会", service_fee_cents=3_000_000)
    db_session.add(project)
    await db_session.commit()
    _login(client)
    payload = {
        "batch_key": "xlsx-2026-09-10",
        "rows": [
            {
                "kind": "COST",
                "scope": "PROJECT",
                "project_id": str(project.id),
                "amount_cents": 400_000,
                "occurred_on": "2026-09-02",
                "category": "印刷",
            },
            {
                "kind": "COST",
                "scope": "PROJECT",
                "project_id": str(project.id),
                "amount_cents": 50_000,
                "occurred_on": "2026-09-03",
                "category": "团队抽成",
            },
            {
                "kind": "INCOME",
                "scope": "PROJECT",
                "project_id": str(project.id),
                "amount_cents": 3_000_000,
                "occurred_on": "2026-09-10",
                "category": "尾款",
            },
        ],
    }
    first = client.post("/api/v1/finance/import", json=payload, headers=_csrf(client))
    second = client.post("/api/v1/finance/import", json=payload, headers=_csrf(client))
    assert first.status_code == 200
    assert first.json()["inserted"] == 3
    assert second.json()["replayed"] is True
    assert second.json()["inserted"] == 0
    rewards = client.get(f"/api/v1/finance/rewards/{project.id}")
    body = rewards.json()
    assert body["cost_cents"] == 400_000
    assert body["surplus_bonus_cents"] == 200_000
    assert body["pool_pay_cents"] == 300_000
    assert body["payroll_on"] == "2026-09-20"
    overview = client.get("/api/v1/finance/overview")
    assert overview.status_code == 200
    assert overview.json()["has_opening_balance"] is False
    month = client.put(
        "/api/v1/finance/month-costs/2026-09",
        json={"amount_cents": 120_000},
        headers=_csrf(client),
    )
    assert month.status_code == 200
    assert month.json()["amount_cents"] == 120_000


@pytest.mark.asyncio
async def test_staff_summary_omits_company_and_income(
    finance_client, db_session: AsyncSession
) -> None:
    client = finance_client
    project = Project(name="星野合作")
    staff = local_user("staff-1", display_name="Staff")
    db_session.add_all([project, staff])
    await db_session.commit()
    _login(client)
    client.post(
        "/api/v1/finance/entries",
        json={
            "kind": "COST",
            "scope": "COMPANY",
            "amount_cents": 800_000,
            "occurred_on": "2026-09-01",
            "category": "房租",
        },
        headers=_csrf(client),
    )
    client.post(
        "/api/v1/finance/entries",
        json={
            "kind": "INCOME",
            "scope": "PROJECT",
            "project_id": str(project.id),
            "amount_cents": 500_000,
            "occurred_on": "2026-09-01",
            "category": "回款",
        },
        headers=_csrf(client),
    )
    client.post(
        "/api/v1/finance/entries",
        json={
            "kind": "COST",
            "scope": "PROJECT",
            "project_id": str(project.id),
            "amount_cents": 1_200_000,
            "occurred_on": "2026-09-02",
            "category": "外包",
        },
        headers=_csrf(client),
    )
    client.cookies.clear()
    _login(client, "staff-1")
    summary = client.get("/api/v1/finance/summary", params={"month": "2026-09"})
    listed = client.get("/api/v1/finance/entries", params={"month": "2026-09"})
    created = client.post(
        "/api/v1/finance/entries",
        json={
            "kind": "COST",
            "scope": "PROJECT",
            "project_id": str(project.id),
            "amount_cents": 1,
            "occurred_on": "2026-09-03",
            "category": "nope",
        },
        headers=_csrf(client),
    )
    body = summary.json()
    payload = listed.json()
    assert summary.status_code == 200
    assert payload == []
    assert not body.get("projects")
    assert created.status_code == 403 and created.json()["error"]["code"] == "FORBIDDEN"


@pytest.mark.asyncio
async def test_owner_adjustment_changes_summary_without_overwriting(
    finance_client, db_session: AsyncSession
) -> None:
    from sqlalchemy import select

    from superboss.modules.finance.models import FinanceEntry

    client = finance_client
    _login(client)
    created = client.post(
        "/api/v1/finance/entries",
        json={
            "kind": "COST",
            "scope": "COMPANY",
            "amount_cents": 800_000,
            "occurred_on": "2026-09-01",
            "category": "房租",
        },
        headers=_csrf(client),
    )
    entry_id = created.json()["id"]
    adjusted = client.post(
        f"/api/v1/finance/entries/{entry_id}/adjustments",
        json={"field": "amount_cents", "new_value": "900000", "reason": "补差"},
        headers=_csrf(client),
    )
    assert adjusted.status_code == 200
    assert adjusted.json()["amount_cents"] == 900_000
    assert adjusted.json()["adjustments"][0]["old_value"] == "800000"
    row = await db_session.scalar(select(FinanceEntry).where(FinanceEntry.id == entry_id))
    assert row is not None and row.amount_cents == 800_000
    summary = client.get("/api/v1/finance/summary", params={"month": "2026-09"})
    assert summary.json()["company"]["cost_cents"] == 900_000


@pytest.mark.asyncio
async def test_manager_sees_company_costs_not_owner_only(
    finance_client, db_session: AsyncSession
) -> None:
    client = finance_client
    db_session.add(local_user("manager-1", display_name="Manager", role=Role.MANAGER))
    await db_session.commit()
    _login(client)
    client.post(
        "/api/v1/finance/entries",
        json={
            "kind": "COST",
            "scope": "COMPANY",
            "amount_cents": 100,
            "occurred_on": "2026-09-01",
            "category": "公开",
        },
        headers=_csrf(client),
    )
    client.post(
        "/api/v1/finance/entries",
        json={
            "kind": "COST",
            "scope": "COMPANY",
            "amount_cents": 200,
            "occurred_on": "2026-09-01",
            "category": "私账",
            "visibility": "OWNER_ONLY",
        },
        headers=_csrf(client),
    )
    client.cookies.clear()
    _login(client, "manager-1")
    listed = client.get("/api/v1/finance/entries", params={"month": "2026-09"})
    names = {item["category"] for item in listed.json()}
    assert names == {"公开"}
    summary = client.get("/api/v1/finance/summary", params={"month": "2026-09"})
    assert summary.json()["company"]["cost_cents"] == 100
