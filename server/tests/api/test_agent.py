"""霜月 chat loop, card confirm, and OWNER lock."""

import json
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from superboss.core.config import Settings
from superboss.core.llm import LLMResult, LLMStreamChunk, LLMToolCall
from superboss.main import create_app
from superboss.modules.files.models import FileState
from superboss.modules.users.models import User
from tests.files.factory import add_folder, make_file
from tests.files.storage import InMemoryObjectStorage
from tests.identity import LOCAL_TEST_PASSWORD, local_user


class ScriptedLLM:
    available = True

    def __init__(self, results: list[LLMResult]) -> None:
        self.results = list(results)

    async def complete(self, messages, tools) -> LLMResult:
        del messages, tools
        if not self.results:
            return LLMResult(content="请确认卡片。")
        return self.results.pop(0)

    async def stream(self, messages, tools) -> AsyncIterator[LLMStreamChunk]:
        result = await self.complete(messages, tools)
        if result.content:
            for piece in result.content:
                yield LLMStreamChunk(content=piece)
        yield LLMStreamChunk(
            done=True,
            tool_calls=result.tool_calls,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
        )


def _sse_events(body: str) -> list[tuple[str, object]]:
    events: list[tuple[str, object]] = []
    for block in body.split("\n\n"):
        if not block.strip():
            continue
        event = "message"
        data: list[str] = []
        for line in block.splitlines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].strip())
        if not data:
            continue
        events.append((event, json.loads("\n".join(data))))
    return events


@pytest_asyncio.fixture
async def agent_client(db_session: AsyncSession, test_settings: Settings, active_owner: User):
    del active_owner
    await db_session.commit()
    llm = ScriptedLLM(
        [
            LLMResult(
                content="",
                tool_calls=[
                    LLMToolCall(
                        id="call-1",
                        name="propose_finance_entry",
                        arguments=json.dumps(
                            {
                                "kind": "COST",
                                "scope": "COMPANY",
                                "amount_cents": 800000,
                                "occurred_on": "2026-09-01",
                                "category": "房租",
                            }
                        ),
                    )
                ]
            ),
            LLMResult(content="请确认这张房租卡片。"),
        ]
    )
    app = create_app(
        test_settings,
        object_storage=InMemoryObjectStorage(),
        enqueue_file_scan=lambda _file_id, _key: None,
        llm_client=llm,
        enqueue_memory_extract=lambda _conversation_id: None,
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


def test_owner_chat_proposes_and_confirms_finance_card(agent_client) -> None:
    client = agent_client
    _login(client)
    created = client.post("/api/v1/agent/conversations", json={}, headers=_csrf(client))
    assert created.status_code == 201
    conversation_id = created.json()["id"]
    turn = client.post(
        f"/api/v1/agent/conversations/{conversation_id}/messages",
        json={"content": "这个月公司房租 8000"},
        headers=_csrf(client),
    )
    assert turn.status_code == 200
    assert turn.json()["offline"] is False
    cards = turn.json()["cards"]
    assert len(cards) == 1
    assert cards[0]["kind"] == "finance_entry"
    assert cards[0]["payload"]["amount_cents"] == 800000
    confirmed = client.post(
        f"/api/v1/agent/cards/{cards[0]['id']}/confirm",
        headers=_csrf(client),
    )
    assert confirmed.status_code == 200, confirmed.json()
    assert confirmed.json()["status"] == "COMMITTED"
    summary = client.get("/api/v1/finance/summary", params={"month": "2026-09"})
    assert summary.json()["company"]["cost_cents"] == 800000


@pytest.mark.asyncio
async def test_staff_cannot_use_agent(agent_client, db_session: AsyncSession) -> None:
    client = agent_client
    db_session.add(local_user("staff-1", display_name="Staff"))
    await db_session.commit()
    _login(client, "staff-1")
    listed = client.get("/api/v1/agent/conversations")
    assert listed.status_code == 403
    assert listed.json()["error"]["code"] == "FORBIDDEN"


def test_soul_defaults_and_preview(agent_client) -> None:
    client = agent_client
    _login(client)
    versions = client.get("/api/v1/agent/soul")
    assert versions.status_code == 200
    assert any(item["is_active"] for item in versions.json())
    preview = client.get("/api/v1/agent/soul/preview")
    assert preview.status_code == 200
    assert "只服务老板" in preview.json()["prompt"]


def test_owner_filters_conversations_by_title(agent_client) -> None:
    client = agent_client
    _login(client)
    headers = _csrf(client)
    assert (
        client.post(
            "/api/v1/agent/conversations",
            json={"title": "星野合作"},
            headers=headers,
        ).status_code
        == 201
    )
    assert (
        client.post(
            "/api/v1/agent/conversations",
            json={"title": "内部房租"},
            headers=headers,
        ).status_code
        == 201
    )
    found = client.get("/api/v1/agent/conversations", params={"q": "星野"})
    assert found.status_code == 200
    assert {item["title"] for item in found.json()} == {"星野合作"}
    empty = client.get("/api/v1/agent/conversations", params={"q": "不存在的会话"})
    assert empty.json() == []


def test_owner_patches_card_before_confirm(agent_client) -> None:
    client = agent_client
    _login(client)
    headers = _csrf(client)
    created = client.post("/api/v1/agent/conversations", json={}, headers=headers)
    conversation_id = created.json()["id"]
    turn = client.post(
        f"/api/v1/agent/conversations/{conversation_id}/messages",
        json={"content": "这个月公司房租 8000"},
        headers=headers,
    )
    card_id = turn.json()["cards"][0]["id"]
    patched = client.patch(
        f"/api/v1/agent/cards/{card_id}",
        json={"payload": {"amount_cents": 900000, "category": "水电"}, "note": "改水电"},
        headers=headers,
    )
    assert patched.status_code == 200
    assert patched.json()["payload"]["amount_cents"] == 900000
    assert patched.json()["payload"]["category"] == "水电"
    confirmed = client.post(f"/api/v1/agent/cards/{card_id}/confirm", headers=headers)
    assert confirmed.status_code == 200, confirmed.json()
    summary = client.get("/api/v1/finance/summary", params={"month": "2026-09"})
    assert summary.json()["company"]["cost_cents"] == 900000
    refused = client.patch(
        f"/api/v1/agent/cards/{card_id}",
        json={"payload": {"category": "再改"}},
        headers=headers,
    )
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "CARD_NOT_OPEN"


def test_owner_chat_stream_emits_tokens_then_done(agent_client) -> None:
    client = agent_client
    _login(client)
    headers = _csrf(client)
    created = client.post("/api/v1/agent/conversations", json={}, headers=headers)
    conversation_id = created.json()["id"]
    response = client.post(
        f"/api/v1/agent/conversations/{conversation_id}/messages/stream",
        json={"content": "这个月公司房租 8000"},
        headers=headers,
    )
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    events = _sse_events(response.text)
    kinds = [kind for kind, _payload in events]
    assert "token" in kinds
    assert kinds[-1] == "done"
    tokens = "".join(
        payload for kind, payload in events if kind == "token" and isinstance(payload, str)
    )
    assert "请确认" in tokens
    done = events[-1][1]
    assert isinstance(done, dict)
    assert done["cards"][0]["kind"] == "finance_entry"
    assert done["offline"] is False


@pytest.mark.asyncio
async def test_chat_inlines_clean_attachment_excerpt(
    agent_client, db_session: AsyncSession, active_owner: User
) -> None:
    client = agent_client
    storage: InMemoryObjectStorage = client.app.state.object_storage
    folder = await add_folder(db_session, active_owner.id)
    file = make_file(
        folder_id=folder.id,
        uploader_id=active_owner.id,
        filename="brief.txt",
        object_key=f"folders/{folder.id}/brief.txt",
        content_type="text/plain",
    )
    db_session.add(file)
    storage.bodies[file.object_key] = "星野合作默认三个里程碑".encode()
    await db_session.commit()
    _login(client)
    headers = _csrf(client)
    created = client.post("/api/v1/agent/conversations", json={}, headers=headers)
    conversation_id = created.json()["id"]
    turn = client.post(
        f"/api/v1/agent/conversations/{conversation_id}/messages",
        json={"content": "看附件", "file_id": str(file.id)},
        headers=headers,
    )
    assert turn.status_code == 200
    messages = client.get(f"/api/v1/agent/conversations/{conversation_id}/messages")
    user = next(item for item in messages.json() if item["role"] == "user")
    assert "brief.txt" in user["content"]
    assert "星野合作" in user["content"]


@pytest.mark.asyncio
async def test_chat_skips_extract_when_attachment_not_clean(
    agent_client, db_session: AsyncSession, active_owner: User
) -> None:
    client = agent_client
    folder = await add_folder(db_session, active_owner.id)
    file = make_file(
        folder_id=folder.id,
        uploader_id=active_owner.id,
        filename="secret.txt",
        object_key=f"folders/{folder.id}/secret.txt",
        content_type="text/plain",
        state=FileState.SCANNING,
    )
    db_session.add(file)
    client.app.state.object_storage.bodies[file.object_key] = "不该出现的正文".encode()
    await db_session.commit()
    _login(client)
    headers = _csrf(client)
    created = client.post("/api/v1/agent/conversations", json={}, headers=headers)
    conversation_id = created.json()["id"]
    turn = client.post(
        f"/api/v1/agent/conversations/{conversation_id}/messages",
        json={"content": "看附件", "file_id": str(file.id)},
        headers=headers,
    )
    assert turn.status_code == 200
    messages = client.get(f"/api/v1/agent/conversations/{conversation_id}/messages")
    user = next(item for item in messages.json() if item["role"] == "user")
    assert "尚未通过扫描" in user["content"]
    assert "不该出现的正文" not in user["content"]
