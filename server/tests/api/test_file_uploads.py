"""File upload request validation behavior."""

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from superboss.core.config import Settings
from superboss.main import create_app
from superboss.modules.projects.models import Project, ProjectMember
from superboss.modules.users.models import Role, User, UserStatus
from tests.files.storage import InMemoryObjectStorage


@pytest_asyncio.fixture
async def file_client(db_session: AsyncSession, test_settings: Settings):
    app = create_app(test_settings)
    app.state.object_storage = InMemoryObjectStorage()
    app.state.enqueue_file_scan = lambda _file_id: None
    with TestClient(app, base_url="https://testserver") as client:
        yield client, app.state.object_storage


def _login(client: TestClient) -> None:
    started = client.get("/api/v1/auth/wecom/start")
    assert client.get("/api/v1/auth/wecom/callback", params={"code": "owner-code", "state": started.json()["state"]}).status_code == 204


@pytest.mark.asyncio
async def test_owner_starts_upload_with_injected_storage(file_client, db_session: AsyncSession) -> None:
    client, storage = file_client
    project = Project(name="HTTP Files")
    db_session.add(project)
    await db_session.commit()
    _login(client)
    response = client.post("/api/v1/files/uploads", json={"project_id": str(project.id), "filename": "x.pdf", "size_bytes": 1, "sha256": "0" * 64, "category": "资料", "file_date": "2026-08-09"}, headers={"X-CSRF-Token": str(client.cookies.get("XSRF-TOKEN")), "Idempotency-Key": "happy", "X-Request-ID": "bba39a39-47ba-4ac5-9250-ccdba1d7f25e"})
    assert response.status_code == 201 and response.headers["X-Request-ID"] == "bba39a39-47ba-4ac5-9250-ccdba1d7f25e"
    assert len(storage.active) == 1


@pytest.mark.asyncio
async def test_assigned_staff_starts_upload(file_client, db_session: AsyncSession) -> None:
    from superboss.modules.files.models import File
    client, _storage = file_client
    staff = User(wecom_userid="staff-1", display_name="Staff", role=Role.STAFF, status=UserStatus.ACTIVE)
    project = Project(name="Staff HTTP")
    db_session.add_all([staff, project]); await db_session.flush(); db_session.add(ProjectMember(project_id=project.id, user_id=staff.id)); await db_session.commit()
    started = client.get("/api/v1/auth/wecom/start")
    assert client.get("/api/v1/auth/wecom/callback", params={"code": "staff-code", "state": started.json()["state"]}).status_code == 204
    response = client.post("/api/v1/files/uploads", json={"project_id": str(project.id), "filename": "x.pdf", "size_bytes": 1, "sha256": "0" * 64, "category": "资料", "file_date": "2026-08-09"}, headers={"X-CSRF-Token": str(client.cookies.get("XSRF-TOKEN")), "Idempotency-Key": "staff-upload"})
    assert response.status_code == 201
    file = await db_session.get(File, response.json()["file_id"])
    assert file is not None and file.project_id == project.id and file.uploader_id == staff.id and file.uploader_kind == "user"


@pytest.mark.asyncio
async def test_foreign_staff_cannot_start_upload(file_client, db_session: AsyncSession) -> None:
    client, storage = file_client
    staff = User(wecom_userid="staff-1", display_name="Staff", role=Role.STAFF, status=UserStatus.ACTIVE)
    target, assigned = Project(name="Foreign target"), Project(name="Foreign assigned")
    db_session.add_all([staff, target, assigned]); await db_session.flush(); db_session.add(ProjectMember(project_id=assigned.id, user_id=staff.id)); await db_session.commit()
    started = client.get("/api/v1/auth/wecom/start"); client.get("/api/v1/auth/wecom/callback", params={"code": "staff-code", "state": started.json()["state"]})
    response = client.post("/api/v1/files/uploads", json={"project_id": str(target.id), "filename": "x.pdf", "size_bytes": 1, "sha256": "0" * 64, "category": "资料", "file_date": "2026-08-09"}, headers={"X-CSRF-Token": str(client.cookies.get("XSRF-TOKEN")), "Idempotency-Key": "foreign-start"})
    assert response.status_code == 403 and response.json()["error"]["code"] == "PROJECT_FORBIDDEN" and storage.active == {}


def test_anonymous_start_uses_authentication_error_before_csrf(file_client) -> None:
    client, storage = file_client
    response = client.post("/api/v1/files/uploads", json={"project_id": "00000000-0000-0000-0000-000000000001", "filename": "x.pdf", "size_bytes": 1, "sha256": "0" * 64, "category": "资料", "file_date": "2026-08-09"}, headers={"Idempotency-Key": "anonymous"})
    assert response.status_code == 401 and response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"
    assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"] and storage.active == {}


@pytest.mark.parametrize("csrf", [None, "wrong"])
def test_browser_start_requires_valid_csrf(file_client, csrf: str | None) -> None:
    client, storage = file_client
    _login(client)
    headers = {"Idempotency-Key": "csrf"}
    if csrf is not None: headers["X-CSRF-Token"] = csrf
    response = client.post("/api/v1/files/uploads", json={"project_id": "00000000-0000-0000-0000-000000000001", "filename": "x.pdf", "size_bytes": 1, "sha256": "0" * 64, "category": "资料", "file_date": "2026-08-09"}, headers=headers)
    assert response.status_code == 403 and response.json()["error"]["code"] == "CSRF_VALIDATION_FAILED" and storage.active == {}


@pytest.mark.asyncio
async def test_header_only_owner_starts_upload_without_csrf(file_client, db_session: AsyncSession) -> None:
    client, storage = file_client
    project = Project(name="Bearer files"); db_session.add(project); await db_session.commit()
    _login(client); token = str(client.cookies.get("access_token")); client.cookies.clear()
    response = client.post("/api/v1/files/uploads", json={"project_id": str(project.id), "filename": "x.pdf", "size_bytes": 1, "sha256": "0" * 64, "category": "资料", "file_date": "2026-08-09"}, headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "bearer"})
    assert response.status_code == 201 and len(storage.active) == 1


def test_invalid_header_only_start_is_unauthenticated(file_client) -> None:
    client, storage = file_client
    response = client.post("/api/v1/files/uploads", json={"project_id": "00000000-0000-0000-0000-000000000001", "filename": "x.pdf", "size_bytes": 1, "sha256": "0" * 64, "category": "资料", "file_date": "2026-08-09"}, headers={"Authorization": "Bearer invalid", "Idempotency-Key": "invalid"})
    assert response.status_code == 401 and response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED" and storage.active == {}


@pytest.mark.asyncio
async def test_revoked_header_only_start_is_unauthenticated(file_client, db_session: AsyncSession) -> None:
    from sqlalchemy import select

    from superboss.modules.auth.models import AuthSession
    client, storage = file_client
    _login(client); token = str(client.cookies.get("access_token"))
    session = await db_session.scalar(select(AuthSession)); assert session is not None
    session.revoked_at = session.created_at; await db_session.commit(); client.cookies.clear()
    response = client.post("/api/v1/files/uploads", json={"project_id": "00000000-0000-0000-0000-000000000001", "filename": "x.pdf", "size_bytes": 1, "sha256": "0" * 64, "category": "资料", "file_date": "2026-08-09"}, headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "revoked"})
    assert response.status_code == 401 and response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED" and storage.active == {}


@pytest.mark.parametrize("change", [{"size_bytes": 0}, {"size_bytes": 100 * 1024 * 1024 + 1}, {"sha256": "A" * 64}, {"sha256": "0" * 63}])
def test_start_rejects_invalid_size_or_sha(file_client, change: dict[str, object]) -> None:
    client, storage = file_client; _login(client)
    body = {"project_id": "00000000-0000-0000-0000-000000000001", "filename": "x.pdf", "size_bytes": 1, "sha256": "0" * 64, "category": "资料", "file_date": "2026-08-09"}; body.update(change)
    response = client.post("/api/v1/files/uploads", json=body, headers={"X-CSRF-Token": str(client.cookies.get("XSRF-TOKEN")), "Idempotency-Key": "validation"})
    assert response.status_code == 422 and response.json()["error"]["code"] == "VALIDATION_ERROR" and storage.active == {}


@pytest.mark.asyncio
async def test_start_accepts_exactly_100_mib(file_client, db_session: AsyncSession) -> None:
    from superboss.modules.files.models import File
    client, storage = file_client; project = Project(name="100 MiB"); db_session.add(project); await db_session.commit(); _login(client)
    response = client.post("/api/v1/files/uploads", json={"project_id": str(project.id), "filename": "x.pdf", "size_bytes": 100 * 1024 * 1024, "sha256": "0" * 64, "category": "资料", "file_date": "2026-08-09"}, headers={"X-CSRF-Token": str(client.cookies.get("XSRF-TOKEN")), "Idempotency-Key": "max"})
    file = await db_session.get(File, response.json()["file_id"])
    assert response.status_code == 201 and file is not None and file.size_bytes == 100 * 1024 * 1024 and len(storage.active) == 1


@pytest.mark.parametrize("content_type", ["", "text/plain\r\nX: y", "text/\x00plain", "invalid", "a" * 256])
def test_start_rejects_unsafe_content_type(file_client, content_type: str) -> None:
    client, storage = file_client; _login(client)
    response = client.post("/api/v1/files/uploads", json={"project_id": "00000000-0000-0000-0000-000000000001", "filename": "x.pdf", "size_bytes": 1, "sha256": "0" * 64, "category": "资料", "file_date": "2026-08-09", "content_type": content_type}, headers={"X-CSRF-Token": str(client.cookies.get("XSRF-TOKEN")), "Idempotency-Key": "content-type"})
    assert response.status_code == 422 and response.json()["error"]["code"] == "VALIDATION_ERROR" and storage.active == {}


@pytest.mark.parametrize("filename", ["", "x" * 1025, "x\x00.pdf", "x\r\ny.pdf", "\x01"])
def test_start_rejects_unsafe_filename(file_client, filename: str) -> None:
    client, storage = file_client; _login(client)
    response = client.post("/api/v1/files/uploads", json={"project_id": "00000000-0000-0000-0000-000000000001", "filename": filename, "size_bytes": 1, "sha256": "0" * 64, "category": "资料", "file_date": "2026-08-09"}, headers={"X-CSRF-Token": str(client.cookies.get("XSRF-TOKEN")), "Idempotency-Key": "filename"})
    assert response.status_code == 422 and response.json()["error"]["code"] == "VALIDATION_ERROR" and storage.active == {}


@pytest.mark.parametrize("category", ["", "x" * 256, "x\x00", "x\r\ny", "\x01"])
def test_start_rejects_unsafe_category(file_client, category: str) -> None:
    client, storage = file_client; _login(client)
    response = client.post("/api/v1/files/uploads", json={"project_id": "00000000-0000-0000-0000-000000000001", "filename": "x.pdf", "size_bytes": 1, "sha256": "0" * 64, "category": category, "file_date": "2026-08-09"}, headers={"X-CSRF-Token": str(client.cookies.get("XSRF-TOKEN")), "Idempotency-Key": "category"})
    assert response.status_code == 422 and response.json()["error"]["code"] == "VALIDATION_ERROR" and storage.active == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["", "x" * 256, "x\x7f"])
async def test_start_rejects_invalid_idempotency_key(file_client, db_session: AsyncSession, key: str) -> None:
    from sqlalchemy import func, select

    from superboss.modules.files.models import File, Upload
    client, storage = file_client; _login(client)
    response = client.post("/api/v1/files/uploads", json={"project_id": "00000000-0000-0000-0000-000000000001", "filename": "x.pdf", "size_bytes": 1, "sha256": "0" * 64, "category": "资料", "file_date": "2026-08-09"}, headers={"X-CSRF-Token": str(client.cookies.get("XSRF-TOKEN")), "Idempotency-Key": key})
    assert response.status_code == 422 and response.json()["error"]["code"] == "VALIDATION_ERROR" and storage.active == {}
    assert await db_session.scalar(select(func.count()).select_from(File)) == 0
    assert await db_session.scalar(select(func.count()).select_from(Upload)) == 0


def test_start_requires_idempotency_key(file_client) -> None:
    client, storage = file_client; _login(client)
    response = client.post("/api/v1/files/uploads", json={"project_id": "00000000-0000-0000-0000-000000000001", "filename": "x.pdf", "size_bytes": 1, "sha256": "0" * 64, "category": "资料", "file_date": "2026-08-09"}, headers={"X-CSRF-Token": str(client.cookies.get("XSRF-TOKEN"))})
    assert response.status_code == 422 and response.json()["error"]["code"] == "VALIDATION_ERROR" and response.json()["error"]["request_id"] == response.headers["X-Request-ID"] and storage.active == {}


@pytest.mark.asyncio
async def test_start_replays_identical_idempotency_key(file_client, db_session: AsyncSession) -> None:
    from sqlalchemy import func, select

    from superboss.modules.files.models import File, Upload
    client, storage = file_client; project = Project(name="Replay"); db_session.add(project); await db_session.commit(); _login(client)
    body = {"project_id": str(project.id), "filename": "x.pdf", "size_bytes": 1, "sha256": "0" * 64, "category": "资料", "file_date": "2026-08-09"}
    headers = {"X-CSRF-Token": str(client.cookies.get("XSRF-TOKEN")), "Idempotency-Key": "replay"}
    first, second = client.post("/api/v1/files/uploads", json=body, headers=headers), client.post("/api/v1/files/uploads", json=body, headers=headers)
    assert first.status_code == second.status_code == 201 and first.json() == second.json() and len(storage.active) == 1
    assert await db_session.scalar(select(func.count()).select_from(File)) == 1 and await db_session.scalar(select(func.count()).select_from(Upload)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [{"filename": "y.pdf"}, {"category": "合同"}, {"file_date": "2026-08-10"}, {"size_bytes": 2}, {"sha256": "1" * 64}, {"content_type": "image/png"}])
async def test_start_rejects_changed_metadata_for_same_key(file_client, db_session: AsyncSession, change: dict[str, object]) -> None:
    from sqlalchemy import func, select

    from superboss.modules.files.models import File, Upload
    client, storage = file_client; project = Project(name="HTTP conflict"); db_session.add(project); await db_session.commit(); _login(client)
    body = {"project_id": str(project.id), "filename": "x.pdf", "size_bytes": 1, "sha256": "0" * 64, "category": "资料", "file_date": "2026-08-09", "content_type": "application/pdf"}; headers = {"X-CSRF-Token": str(client.cookies.get("XSRF-TOKEN")), "Idempotency-Key": "conflict"}
    assert client.post("/api/v1/files/uploads", json=body, headers=headers).status_code == 201
    body.update(change); response = client.post("/api/v1/files/uploads", json=body, headers=headers)
    assert response.status_code == 409 and response.json()["error"]["request_id"] == response.headers["X-Request-ID"] and len(storage.active) == 1
    assert await db_session.scalar(select(func.count()).select_from(File)) == 1 and await db_session.scalar(select(func.count()).select_from(Upload)) == 1


@pytest.mark.asyncio
async def test_owner_presigns_first_upload_part(file_client, db_session: AsyncSession) -> None:
    from superboss.modules.files.models import File, Upload
    client, storage = file_client; project = Project(name="Part HTTP"); db_session.add(project); await db_session.commit(); _login(client)
    headers = {"X-CSRF-Token": str(client.cookies.get("XSRF-TOKEN")), "Idempotency-Key": "part"}
    started = client.post("/api/v1/files/uploads", json={"project_id": str(project.id), "filename": "x.pdf", "size_bytes": 1, "sha256": "0" * 64, "category": "资料", "file_date": "2026-08-09"}, headers=headers)
    upload_id = started.json()["upload_id"]; upload = await db_session.get(Upload, upload_id); assert upload is not None
    response = client.post(f"/api/v1/files/uploads/{upload_id}/parts/1", headers={"X-CSRF-Token": str(client.cookies.get("XSRF-TOKEN"))})
    file = await db_session.get(File, upload.file_id)
    assert response.status_code == 200 and response.json() == {"url": f"memory://part/{upload.multipart_id}/1"} and storage.expiries[-1] == 900 and file is not None and file.state.value == "UPLOADING"


@pytest.mark.asyncio
@pytest.mark.parametrize("part_number", [0, 10_001])
async def test_part_rejects_out_of_range_number(file_client, db_session: AsyncSession, part_number: int) -> None:
    from superboss.modules.files.models import File, Upload
    client, storage = file_client; project = Project(name=f"Part {part_number}"); db_session.add(project); await db_session.commit(); _login(client)
    headers = {"X-CSRF-Token": str(client.cookies.get("XSRF-TOKEN")), "Idempotency-Key": f"part-{part_number}"}
    started = client.post("/api/v1/files/uploads", json={"project_id": str(project.id), "filename": "x.pdf", "size_bytes": 1, "sha256": "0" * 64, "category": "资料", "file_date": "2026-08-09"}, headers=headers)
    upload = await db_session.get(Upload, started.json()["upload_id"]); assert upload is not None
    before = list(storage.expiries); response = client.post(f"/api/v1/files/uploads/{upload.id}/parts/{part_number}", headers={"X-CSRF-Token": str(client.cookies.get("XSRF-TOKEN"))})
    file = await db_session.get(File, upload.file_id)
    assert response.status_code == 422 and response.json()["error"]["code"] == "VALIDATION_ERROR" and response.json()["error"]["request_id"] == response.headers["X-Request-ID"] and storage.expiries == before and file is not None and file.state.value == "UPLOADING"


def test_part_missing_upload_returns_not_found(file_client) -> None:
    from uuid import uuid4
    client, storage = file_client; _login(client)
    response = client.post(f"/api/v1/files/uploads/{uuid4()}/parts/1", headers={"X-CSRF-Token": str(client.cookies.get("XSRF-TOKEN"))})
    assert response.status_code == 404 and response.json()["error"]["request_id"] == response.headers["X-Request-ID"] and storage.expiries == []


@pytest.mark.asyncio
async def test_part_rejects_quarantined_file(file_client, db_session: AsyncSession) -> None:
    from superboss.modules.files.models import File, FileState, Upload
    client, storage = file_client; project = Project(name="Part state"); db_session.add(project); await db_session.commit(); _login(client)
    headers = {"X-CSRF-Token": str(client.cookies.get("XSRF-TOKEN")), "Idempotency-Key": "part-state"}
    started = client.post("/api/v1/files/uploads", json={"project_id": str(project.id), "filename": "x.pdf", "size_bytes": 1, "sha256": "0" * 64, "category": "资料", "file_date": "2026-08-09"}, headers=headers)
    upload = await db_session.get(Upload, started.json()["upload_id"]); assert upload is not None
    file = await db_session.get(File, upload.file_id); assert file is not None; file.state = FileState.QUARANTINED; await db_session.commit()
    before = list(storage.expiries); response = client.post(f"/api/v1/files/uploads/{upload.id}/parts/1", headers={"X-CSRF-Token": str(client.cookies.get("XSRF-TOKEN"))})
    assert response.status_code == 409 and response.json()["error"]["request_id"] == response.headers["X-Request-ID"] and storage.expiries == before


@pytest.mark.asyncio
async def test_foreign_staff_cannot_presign_upload_part(file_client, db_session: AsyncSession) -> None:
    from superboss.modules.files.models import File, Upload
    client, storage = file_client; target, assigned = Project(name="Part target"), Project(name="Part assigned"); staff = User(wecom_userid="staff-1", display_name="Staff", role=Role.STAFF, status=UserStatus.ACTIVE)
    db_session.add_all([target, assigned, staff]); await db_session.flush(); db_session.add(ProjectMember(project_id=assigned.id, user_id=staff.id)); await db_session.commit(); _login(client)
    headers = {"X-CSRF-Token": str(client.cookies.get("XSRF-TOKEN")), "Idempotency-Key": "foreign-part"}
    started = client.post("/api/v1/files/uploads", json={"project_id": str(target.id), "filename": "x.pdf", "size_bytes": 1, "sha256": "0" * 64, "category": "资料", "file_date": "2026-08-09"}, headers=headers); upload = await db_session.get(Upload, started.json()["upload_id"]); assert upload is not None
    client.cookies.clear(); begin = client.get("/api/v1/auth/wecom/start"); client.get("/api/v1/auth/wecom/callback", params={"code": "staff-code", "state": begin.json()["state"]})
    before = list(storage.expiries); response = client.post(f"/api/v1/files/uploads/{upload.id}/parts/1", headers={"X-CSRF-Token": str(client.cookies.get("XSRF-TOKEN"))}); file = await db_session.get(File, upload.file_id)
    assert response.status_code == 403 and response.json()["error"]["code"] == "PROJECT_FORBIDDEN" and response.json()["error"]["request_id"] == response.headers["X-Request-ID"] and storage.expiries == before and file is not None and file.state.value == "UPLOADING"


def test_anonymous_part_uses_authentication_error_before_csrf(file_client) -> None:
    from uuid import uuid4
    client, storage = file_client; response = client.post(f"/api/v1/files/uploads/{uuid4()}/parts/1")
    assert response.status_code == 401 and response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED" and response.json()["error"]["request_id"] == response.headers["X-Request-ID"] and storage.expiries == []


@pytest.mark.parametrize("csrf", [None, "wrong"])
def test_owner_part_requires_valid_csrf(file_client, csrf: str | None) -> None:
    from uuid import uuid4
    client, storage = file_client; _login(client); headers = {} if csrf is None else {"X-CSRF-Token": csrf}
    response = client.post(f"/api/v1/files/uploads/{uuid4()}/parts/1", headers=headers)
    assert response.status_code == 403 and response.json()["error"]["code"] == "CSRF_VALIDATION_FAILED" and response.json()["error"]["request_id"] == response.headers["X-Request-ID"] and storage.expiries == []


@pytest.mark.asyncio
async def test_complete_dispatches_after_quarantine_commit(file_client, db_session: AsyncSession) -> None:
    from uuid import UUID

    from sqlalchemy import select
    from superboss.modules.audit.models import AuditLog
    from superboss.modules.files.models import File, Upload
    client, storage = file_client; app = client.app; project = Project(name="Complete dispatch"); db_session.add(project); await db_session.commit(); _login(client)
    observed: list[tuple[UUID, str]] = []
    async def dispatch(file_id: UUID) -> None:
        async with app.state.session_factory() as session:
            file = await session.get(File, file_id); assert file is not None; observed.append((file_id, file.state.value))
    app.state.enqueue_file_scan = dispatch; storage.complete_size = 2
    headers = {"X-CSRF-Token": str(client.cookies.get("XSRF-TOKEN")), "Idempotency-Key": "complete-dispatch"}
    started = client.post("/api/v1/files/uploads", json={"project_id": str(project.id), "filename": "x.pdf", "size_bytes": 2, "sha256": "0" * 64, "category": "资料", "file_date": "2026-08-09"}, headers=headers); upload = await db_session.get(Upload, started.json()["upload_id"]); assert upload is not None
    request_id = "bba39a39-47ba-4ac5-9250-ccdba1d7f25e"
    response = client.post(f"/api/v1/files/uploads/{upload.id}/complete", json={"parts": [{"part_number": 2, "etag": "b"}, {"part_number": 1, "etag": "a"}]}, headers={"X-CSRF-Token": str(client.cookies.get("XSRF-TOKEN")), "X-Request-ID": request_id})
    file = await db_session.get(File, upload.file_id)
    assert response.status_code == 200 and response.json() == {"file_id": str(upload.file_id), "state": "QUARANTINED"} and file is not None and file.state.value == "QUARANTINED" and storage.completed[upload.multipart_id][0].part_number == 1 and upload.multipart_id not in storage.active and observed == [(upload.file_id, "QUARANTINED")]
    events = list((await db_session.scalars(select(AuditLog))).all())
    assert len(events) == 1 and events[0].action == "file.upload.complete" and events[0].outcome == "SUCCESS" and events[0].object_type == "file" and events[0].object_id == upload.file_id and events[0].project_id == project.id and str(events[0].request_id) == request_id and events[0].actor_kind == "user" and events[0].metadata_json["state"] == "QUARANTINED" and events[0].metadata_json["size_bytes"] == 2 and events[0].metadata_json["actor_role"] == "OWNER"


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["x", "!" * 255])
async def test_start_accepts_idempotency_key_boundaries(file_client, db_session: AsyncSession, key: str) -> None:
    client, storage = file_client; project = Project(name=f"Key {len(key)}"); db_session.add(project); await db_session.commit(); _login(client)
    response = client.post("/api/v1/files/uploads", json={"project_id": str(project.id), "filename": "x.pdf", "size_bytes": 1, "sha256": "0" * 64, "category": "资料", "file_date": "2026-08-09"}, headers={"X-CSRF-Token": str(client.cookies.get("XSRF-TOKEN")), "Idempotency-Key": key})
    assert response.status_code == 201 and len(storage.active) == 1
from pydantic import ValidationError


def test_upload_rejects_size_larger_than_100_mib() -> None:
    """Removing the upper bound would accept an oversized direct upload."""
    from superboss.modules.files.schemas import UploadStart

    with pytest.raises(ValidationError):
        UploadStart(
            project_id="00000000-0000-0000-0000-000000000001",
            filename="x.pdf",
            size_bytes=100 * 1024 * 1024 + 1,
            sha256="0" * 64,
            category="资料",
            file_date="2026-08-09",
        )


def test_upload_rejects_uppercase_sha256() -> None:
    """Relaxing the digest contract would accept non-canonical checksums."""
    from superboss.modules.files.schemas import UploadStart

    with pytest.raises(ValidationError):
        UploadStart(
            project_id="00000000-0000-0000-0000-000000000001",
            filename="x.pdf",
            size_bytes=1,
            sha256="A" * 64,
            category="资料",
            file_date="2026-08-09",
        )


@pytest.mark.parametrize("value", [" ", "a\r\nb", "\x00name"])
def test_upload_rejects_control_or_blank_file_metadata(value: str) -> None:
    """Dropping text hygiene would permit headers/keys with control characters."""
    from superboss.modules.files.schemas import UploadStart

    with pytest.raises(ValidationError):
        UploadStart(
            project_id="00000000-0000-0000-0000-000000000001",
            filename=value,
            size_bytes=1,
            sha256="0" * 64,
            category="资料",
            file_date="2026-08-09",
        )


@pytest.mark.parametrize("part", [0, 10_001])
def test_completed_part_rejects_s3_outside_range(part: int) -> None:
    from superboss.modules.files.schemas import PartComplete
    with pytest.raises(ValidationError): PartComplete(part_number=part, etag="etag")


@pytest.mark.parametrize("etag", [" ", "x\r\ny", "x\x00y"])
def test_completed_part_rejects_unsafe_etag(etag: str) -> None:
    from superboss.modules.files.schemas import PartComplete
    with pytest.raises(ValidationError): PartComplete(part_number=1, etag=etag)


def test_upload_complete_rejects_empty_and_duplicate_parts() -> None:
    from superboss.modules.files.schemas import UploadComplete
    with pytest.raises(ValidationError): UploadComplete(parts=[])
    with pytest.raises(ValidationError): UploadComplete(parts=[{"part_number": 1, "etag": "a"}, {"part_number": 1, "etag": "b"}])


def test_upload_complete_canonicalizes_part_order() -> None:
    from superboss.modules.files.schemas import UploadComplete
    complete = UploadComplete(parts=[{"part_number": 2, "etag": "b"}, {"part_number": 1, "etag": "a"}])
    assert [part.part_number for part in complete.parts] == [1, 2]
