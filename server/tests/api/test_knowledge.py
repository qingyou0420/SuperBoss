"""Knowledge chain fields: stage, reason, canonical flag."""

import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from superboss.core.config import Settings
from superboss.main import create_app
from superboss.modules.users.models import User
from tests.identity import LOCAL_TEST_PASSWORD


@pytest_asyncio.fixture
async def knowledge_client(db_session: AsyncSession, test_settings: Settings, active_owner: User):
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


def test_knowledge_stage_chain_and_canonical(knowledge_client: TestClient) -> None:
    client = knowledge_client
    _login(client)
    created = client.post(
        "/api/v1/knowledge",
        json={
            "title": "候选人名单公示定稿",
            "body_md": "使用经确认的名单。",
            "stage_title": "候选人名单公示",
            "change_reason": "社区要求增加照片说明",
            "is_canonical": True,
        },
        headers=_csrf(client),
    )
    assert created.status_code == 201
    body = created.json()
    assert body["stage_title"] == "候选人名单公示"
    assert body["is_canonical"] is True
    published = client.patch(
        f"/api/v1/knowledge/{body['id']}",
        json={"status": "PUBLISHED"},
        headers=_csrf(client),
    )
    assert published.status_code == 200
    listed = client.get("/api/v1/knowledge", params={"stage": "候选人名单公示"})
    assert listed.status_code == 200
    assert listed.json()[0]["title"] == "候选人名单公示定稿"
    empty = client.get("/api/v1/knowledge", params={"stage": "投票与现场执行"})
    assert empty.json() == []
