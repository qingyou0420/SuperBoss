"""Assembly project nodes, shift, and completion gates."""

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from superboss.core.config import Settings
from superboss.main import create_app
from superboss.modules.users.models import User
from tests.identity import LOCAL_TEST_PASSWORD, local_user


@pytest_asyncio.fixture
async def workflow_client(db_session: AsyncSession, test_settings: Settings, active_owner: User):
    del active_owner
    await db_session.commit()
    app = create_app(test_settings)
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
async def test_nodes_generate_shift_keeps_done_and_requires_photos(
    workflow_client: TestClient, db_session: AsyncSession, active_owner: User
) -> None:
    from superboss.modules.files.models import FileState, FolderVisibility
    from tests.files.factory import add_folder, make_file

    client = workflow_client
    _login(client)
    created = client.post(
        "/api/v1/projects",
        json={"name": "云栖里大会", "starts_on": "2026-09-01", "service_fee_cents": 3_000_000},
        headers=_csrf(client),
    )
    assert created.status_code == 201
    project = created.json()
    nodes = project["nodes"]
    assert len(nodes) == 7
    first = nodes[0]
    photo = next(item for item in nodes if item["photo_required"] is True)
    folder = await add_folder(db_session, active_owner.id, visibility=FolderVisibility.ALL)
    document = make_file(
        folder_id=folder.id,
        uploader_id=active_owner.id,
        filename="筹备工作安排.pdf",
        content_type="application/pdf",
        state=FileState.CLEAN,
    )
    db_session.add(document)
    await db_session.commit()
    done = client.post(
        f"/api/v1/projects/{project['id']}/nodes/{first['id']}/complete",
        json={"evidence": [str(document.id)]},
        headers=_csrf(client),
    )
    assert done.status_code == 200
    assert done.json()["nodes"][0]["status"] == "DONE"
    blocked = client.post(
        f"/api/v1/projects/{project['id']}/nodes/{photo['id']}/complete",
        json={"evidence": []},
        headers=_csrf(client),
    )
    assert blocked.status_code == 409
    old_end = done.json()["nodes"][2]["planned_end"]
    shifted = client.post(
        f"/api/v1/projects/{project['id']}/schedule",
        json={"node_id": photo["id"], "days": 5, "reason": "社区改期"},
        headers=_csrf(client),
    )
    assert shifted.status_code == 200
    body = shifted.json()
    assert body["nodes"][0]["planned_end"] == done.json()["nodes"][0]["planned_end"]
    assert body["nodes"][2]["planned_end"] != old_end
    assert body["schedule_changes"]


@pytest.mark.asyncio
async def test_staff_cannot_shift_unless_lead(
    workflow_client: TestClient, db_session: AsyncSession
) -> None:
    client = workflow_client
    staff = local_user("staff-1", display_name="Staff")
    db_session.add(staff)
    await db_session.commit()
    _login(client)
    created = client.post(
        "/api/v1/projects",
        json={"name": "澄湖花园大会", "starts_on": "2026-09-01"},
        headers=_csrf(client),
    )
    assert created.status_code == 201
    project_id = created.json()["id"]
    node_id = created.json()["nodes"][0]["id"]
    client.cookies.clear()
    _login(client, "staff-1")
    denied = client.post(
        f"/api/v1/projects/{project_id}/schedule",
        json={"node_id": node_id, "days": 5, "reason": "不行"},
        headers=_csrf(client),
    )
    assert denied.status_code == 403


@pytest.mark.asyncio
async def test_staff_lead_can_shift(workflow_client: TestClient, db_session: AsyncSession) -> None:
    client = workflow_client
    staff = local_user("staff-lead", display_name="Lead")
    db_session.add(staff)
    await db_session.commit()
    _login(client)
    created = client.post(
        "/api/v1/projects",
        json={"name": "港城御龙湾大会", "starts_on": "2026-09-01"},
        headers=_csrf(client),
    )
    assert created.status_code == 201
    project_id = created.json()["id"]
    node_id = created.json()["nodes"][0]["id"]
    assigned = client.patch(
        f"/api/v1/projects/{project_id}",
        json={"lead_user_id": str(staff.id)},
        headers=_csrf(client),
    )
    assert assigned.status_code == 200
    client.cookies.clear()
    _login(client, "staff-lead")
    shifted = client.post(
        f"/api/v1/projects/{project_id}/schedule",
        json={"node_id": node_id, "days": 5, "reason": "社区改期"},
        headers=_csrf(client),
    )
    assert shifted.status_code == 200
    assert shifted.json()["schedule_changes"]
