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


@pytest.mark.parametrize("key", ["", "x" * 256, "x\x7f"])
def test_start_rejects_invalid_idempotency_key(file_client, key: str) -> None:
    client, storage = file_client; _login(client)
    response = client.post("/api/v1/files/uploads", json={"project_id": "00000000-0000-0000-0000-000000000001", "filename": "x.pdf", "size_bytes": 1, "sha256": "0" * 64, "category": "资料", "file_date": "2026-08-09"}, headers={"X-CSRF-Token": str(client.cookies.get("XSRF-TOKEN")), "Idempotency-Key": key})
    assert response.status_code == 422 and response.json()["error"]["code"] == "VALIDATION_ERROR" and storage.active == {}


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
