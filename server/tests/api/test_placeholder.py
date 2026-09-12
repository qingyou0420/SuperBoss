"""Placeholder seed is idempotent and marked as replaceable."""

import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from superboss.core.config import Settings
from superboss.main import create_app
from superboss.modules.users.models import User
from tests.identity import LOCAL_TEST_PASSWORD


@pytest_asyncio.fixture
async def placeholder_client(db_session: AsyncSession, test_settings: Settings, active_owner: User):
    del active_owner
    await db_session.commit()
    app = create_app(test_settings)
    with TestClient(app, base_url="https://testserver") as client:
        yield client


def _login(client: TestClient) -> None:
    assert client.get("/api/v1/auth/csrf").status_code == 204
    assert (
        client.post(
            "/api/v1/auth/login",
            json={"username": "owner", "password": LOCAL_TEST_PASSWORD},
            headers={"X-CSRF-Token": str(client.cookies.get("XSRF-TOKEN"))},
        ).status_code
        == 204
    )


def _csrf(client: TestClient) -> dict[str, str]:
    return {"X-CSRF-Token": str(client.cookies.get("XSRF-TOKEN"))}


def test_placeholder_seed_fills_workflow_knowledge_and_finance(
    placeholder_client: TestClient,
) -> None:
    client = placeholder_client
    _login(client)
    first = client.post("/api/v1/placeholder/seed", headers=_csrf(client))
    second = client.post("/api/v1/placeholder/seed", headers=_csrf(client))
    assert first.status_code == 200 and second.status_code == 200
    projects = client.get("/api/v1/projects").json()
    names = [item["name"] for item in projects]
    assert names.count("【占位】云栖里业主大会") == 1
    done = next(item for item in projects if item["name"] == "【占位】云栖里业主大会")
    assert done["progress_percent"] == 100
    assert done["schedule_changes"]
    knowledge = client.get("/api/v1/knowledge").json()
    assert any(
        item["is_canonical"] and item["stage_title"] == "候选人名单公示" for item in knowledge
    )
    rewards = client.get(f"/api/v1/finance/rewards/{done['id']}").json()
    assert rewards["surplus_bonus_cents"] == 200_000
    assert rewards["pool_pay_cents"] == 300_000
    assert rewards["payroll_on"] == "2026-09-20"
    assert len(rewards["allocations"]) == 5
    overview = client.get("/api/v1/finance/overview").json()
    assert overview["has_opening_balance"] is True
    assert overview["placeholder"] is True
    assert overview["opening_balance_cents"] == 50_000_000
    listed = client.get("/api/v1/directory", params={"q": "占位"}).json()
    assert listed["total"] >= 2
    extra = listed["items"][0]["extra"]
    assert extra.get("定位")
    assert "经纬度" in extra.get("定位", "") or extra.get("_placeholder") is True
