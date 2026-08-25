"""HTTP contracts for device-scoped K3 intake and the OWNER import list."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from superboss.core.actors import Actor
from superboss.core.config import Settings
from superboss.main import create_app
from superboss.modules.devices.service import DeviceService, DeviceTokenPair
from superboss.modules.files.models import File
from superboss.modules.files.storage import ObjectMetadata
from superboss.modules.imports.schemas import ImportJobCreate
from superboss.modules.imports.service import ImportService
from superboss.modules.projects.models import Project
from superboss.modules.users.models import User
from tests.files.storage import InMemoryObjectStorage
from tests.identity import LOCAL_TEST_PASSWORD

DEVICE_SCOPES = frozenset(
    {"imports:create", "imports:upload", "imports:submit", "imports:read-own"}
)
JOB_RESPONSE_KEYS = {
    "id",
    "project_id",
    "local_task_id",
    "external_document_reference",
    "base_sha256",
    "status",
    "result_code",
    "k3_result",
    "submitted_at",
    "created_at",
    "updated_at",
    "attachments",
}
SUBMIT_RESPONSE_KEYS = {
    "id",
    "status",
    "result_code",
    "submitted_at",
    "updated_at",
}
OWNER_SUMMARY_KEYS = {
    "id",
    "project_id",
    "local_task_id",
    "external_document_reference",
    "model_label",
    "status",
    "result_code",
    "submitted_at",
    "created_at",
    "updated_at",
    "attachments",
}
ATTACHMENT_RESPONSE_KEYS = {"id", "file_id", "upload_id", "kind", "file_state"}
K3_RESPONSE_KEYS = {
    "model_label",
    "processed_at",
    "modification_details",
    "knowledge_points",
    "risks",
    "suggested_title",
    "suggested_tags",
}
FORBIDDEN_RESPONSE_KEYS = {
    "access_token",
    "refresh_token",
    "refresh_token_hash",
    "code_hash",
    "multipart_id",
    "object_key",
    "etag",
    "cookie",
    "canonical_manifest_json",
    "manifest_fingerprint",
    "file_upload_lifecycle",
}


@dataclass
class ImportApiStorage(InMemoryObjectStorage):
    stat_calls: int = 0
    part_calls: int = 0
    part_url_override: str | None = None

    async def stat_object(self, object_key: str) -> ObjectMetadata | None:
        self.stat_calls += 1
        return await super().stat_object(object_key)

    async def presign_upload_part(
        self,
        object_key: str,
        multipart_id: str,
        part_number: int,
        expires_seconds: int,
    ) -> str:
        self.part_calls += 1
        if self.part_url_override is not None:
            self.expiries.append(expires_seconds)
            return self.part_url_override
        return await super().presign_upload_part(
            object_key,
            multipart_id,
            part_number,
            expires_seconds,
        )


@dataclass
class ImportApiContext:
    client: TestClient
    storage: ImportApiStorage
    dispatched: list[tuple[UUID, UUID]] = field(default_factory=list)


@dataclass(frozen=True)
class SeededImport:
    job_id: UUID
    attachment_id: UUID
    file_id: UUID
    upload_id: UUID


@pytest_asyncio.fixture
async def import_api(
    db_session: AsyncSession,
    test_settings: Settings,
    active_owner: User,
) -> AsyncIterator[ImportApiContext]:
    del active_owner
    await db_session.commit()
    storage = ImportApiStorage(complete_size=1)
    dispatched: list[tuple[UUID, UUID]] = []

    def enqueue(file_id: UUID, delivery_key: UUID) -> None:
        dispatched.append((file_id, delivery_key))

    app = create_app(
        test_settings,
        object_storage=storage,
        enqueue_file_scan=enqueue,
    )
    with TestClient(
        app,
        base_url="https://testserver",
        raise_server_exceptions=False,
    ) as client:
        yield ImportApiContext(client, storage, dispatched)


def _factory(db_session: AsyncSession) -> async_sessionmaker[AsyncSession]:
    assert db_session.bind is not None
    return async_sessionmaker(db_session.bind, expire_on_commit=False)


def _login(client: TestClient, code: str = "owner-code") -> None:
    username = {"owner-code": "owner", "staff-code": "staff-1"}.get(code, code)
    assert client.get("/api/v1/auth/csrf").status_code == 204
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": LOCAL_TEST_PASSWORD},
        headers=_csrf(client),
    )
    assert response.status_code == 204


def _csrf(client: TestClient) -> dict[str, str]:
    return {"X-CSRF-Token": str(client.cookies.get("XSRF-TOKEN"))}


def _device_headers(pair: DeviceTokenPair, *, request_id: UUID | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {pair.access_token}"}
    if request_id is not None:
        headers["X-Request-ID"] = str(request_id)
    return headers


def _assert_error(response: httpx.Response, status: int, code: str) -> None:
    assert response.status_code == status
    body = response.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "request_id"}
    assert body["error"]["code"] == code
    assert body["error"]["request_id"] == response.headers["X-Request-ID"]
    serialized = str(body).lower()
    assert "detail" not in body and "trace" not in serialized
    assert all(secret not in serialized for secret in ("authorization", "cookie", "multipart"))


def _body(project_id: UUID, suffix: str = "api") -> dict[str, object]:
    return {
        "project_id": str(project_id),
        "local_task_id": f"local-{suffix}",
        "external_document_reference": f"external-{suffix}",
        "base_sha256": None,
        "k3_result": {
            "model_label": f"K3-{suffix}",
            "processed_at": "2026-08-09T08:30:00+08:00",
            "modification_details": [f"modified-{suffix}"],
            "knowledge_points": [f"knowledge-{suffix}"],
            "risks": [f"risk-{suffix}"],
            "suggested_title": f"title-{suffix}",
            "suggested_tags": [f"tag-{suffix}"],
        },
        "attachments": [
            {
                "kind": "K3_RAW",
                "filename": f"k3-{suffix}.json",
                "size_bytes": 1,
                "sha256": "a" * 64,
                "content_type": "application/json",
            }
        ],
    }


async def _pair(
    context: ImportApiContext,
    db_session: AsyncSession,
    owner_id: UUID,
    project_id: UUID,
    *,
    name: str = "Import-PC",
) -> DeviceTokenPair:
    service = DeviceService(_factory(db_session), context.client.app.state.settings)
    issue = await service.create_pairing_code(
        owner_id,
        [project_id],
        request_id=uuid4(),
    )
    return await service.pair(issue.raw_code, name, request_id=uuid4())


async def _seed_import(
    context: ImportApiContext,
    db_session: AsyncSession,
    pair: DeviceTokenPair,
    project_id: UUID,
    *,
    suffix: str,
) -> SeededImport:
    result = await ImportService(_factory(db_session), context.storage).create(
        Actor("device", pair.device_id, None, frozenset({project_id}), DEVICE_SCOPES),
        ImportJobCreate.model_validate(_body(project_id, suffix)),
        f"seed-{suffix}",
        request_id=uuid4(),
    )
    assert len(result.attachments) == 1
    attachment = result.attachments[0]
    return SeededImport(result.id, attachment.id, attachment.file_id, attachment.upload_id)


def _storage_calls(storage: ImportApiStorage) -> tuple[int, ...]:
    return (
        storage.create_calls,
        storage.list_calls,
        storage.stat_calls,
        storage.part_calls,
        storage.complete_calls,
        len(storage.expiries),
        len(storage.objects),
        len(storage.completed),
        len(storage.deleted),
    )


def _assert_no_forbidden_keys(value: object) -> None:
    if isinstance(value, dict):
        assert not (set(value) & FORBIDDEN_RESPONSE_KEYS)
        for nested in value.values():
            _assert_no_forbidden_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            _assert_no_forbidden_keys(nested)


def _assert_timestamp(value: str | None, *, optional: bool = False) -> None:
    if value is None:
        assert optional
        return
    parsed = datetime.fromisoformat(value)
    assert parsed.tzinfo is not None and parsed.utcoffset() is not None


def _assert_attachment_shape(value: dict[str, object]) -> None:
    assert set(value) == ATTACHMENT_RESPONSE_KEYS
    UUID(str(value["id"]))
    UUID(str(value["file_id"]))
    UUID(str(value["upload_id"]))
    assert value["kind"] in {"ORIGINAL", "REVISED", "K3_RAW"}
    assert value["file_state"] in {
        "UPLOADING",
        "QUARANTINED",
        "SCANNING",
        "CLEAN",
        "INFECTED",
        "FAILED",
    }


def _assert_job_shape(value: dict[str, object]) -> None:
    assert set(value) == JOB_RESPONSE_KEYS
    UUID(str(value["id"]))
    UUID(str(value["project_id"]))
    assert isinstance(value["local_task_id"], str) and len(value["local_task_id"]) <= 255
    external = value["external_document_reference"]
    assert external is None or isinstance(external, str) and len(external) <= 1024
    base = value["base_sha256"]
    assert base is None or isinstance(base, str) and len(base) == 64
    assert value["status"] in {"UPLOADING", "SCANNING", "RECEIVED", "REJECTED", "CONFLICT"}
    result = value["result_code"]
    assert result is None or isinstance(result, str) and 1 <= len(result) <= 64
    k3 = value["k3_result"]
    assert isinstance(k3, dict) and set(k3) == K3_RESPONSE_KEYS
    assert isinstance(k3["processed_at"], str)
    _assert_timestamp(k3["processed_at"])
    attachments = value["attachments"]
    assert isinstance(attachments, list) and 1 <= len(attachments) <= 3
    for attachment in attachments:
        assert isinstance(attachment, dict)
        _assert_attachment_shape(attachment)
    submitted = value["submitted_at"]
    assert submitted is None or isinstance(submitted, str)
    _assert_timestamp(submitted, optional=True)
    assert isinstance(value["created_at"], str)
    assert isinstance(value["updated_at"], str)
    _assert_timestamp(value["created_at"])
    _assert_timestamp(value["updated_at"])
    _assert_no_forbidden_keys(value)


def _assert_submit_shape(value: dict[str, object]) -> None:
    assert set(value) == SUBMIT_RESPONSE_KEYS
    UUID(str(value["id"]))
    assert value["status"] in {"SCANNING", "RECEIVED", "REJECTED", "CONFLICT"}
    result = value["result_code"]
    assert result is None or isinstance(result, str) and 1 <= len(result) <= 64
    assert isinstance(value["submitted_at"], str)
    assert isinstance(value["updated_at"], str)
    _assert_timestamp(value["submitted_at"])
    _assert_timestamp(value["updated_at"])
    _assert_no_forbidden_keys(value)


@pytest.mark.asyncio

@pytest.mark.asyncio
async def test_device_bearer_completes_the_import_flow_with_exact_safe_responses(
    import_api: ImportApiContext,
    db_session: AsyncSession,
    active_owner: User,
) -> None:
    project = Project(name="Import API happy path")
    db_session.add(project)
    await db_session.commit()
    pair = await _pair(import_api, db_session, active_owner.id, project.id)
    headers = {
        **_device_headers(pair),
        "Idempotency-Key": "http-happy-path",
    }

    created = import_api.client.post(
        "/api/v1/device/import-jobs",
        json=_body(project.id, "happy-secret"),
        headers=headers,
    )
    assert created.status_code == 201
    created_body = created.json()
    _assert_job_shape(created_body)
    assert created_body["status"] == "UPLOADING"
    assert created_body["project_id"] == str(project.id)
    attachment = created_body["attachments"][0]
    job_id = UUID(created_body["id"])
    attachment_id = UUID(attachment["id"])

    db_session.expire_all()
    file = await db_session.get(File, UUID(attachment["file_id"]))
    upload = await db_session.get(File, UUID(attachment["upload_id"]))
    assert file is not None and upload is not None
    serialized_create = created.text
    assert file.object_key not in serialized_create
    assert upload.multipart_id is not None and upload.multipart_id not in serialized_create
    assert pair.access_token not in serialized_create and "happy-secret" in serialized_create

    part = import_api.client.post(
        f"/api/v1/device/import-jobs/{job_id}/attachments/{attachment_id}/parts/1",
        headers=_device_headers(pair),
    )
    assert part.status_code == 200 and set(part.json()) == {"url"}
    assert isinstance(part.json()["url"], str)
    assert 1 <= len(part.json()["url"].encode("utf-8")) <= 4096
    assert all(ord(character) >= 32 and ord(character) != 127 for character in part.json()["url"])
    assert import_api.storage.expiries[-1] == 900

    completed = import_api.client.post(
        f"/api/v1/device/import-jobs/{job_id}/attachments/{attachment_id}/complete",
        json={"parts": [{"part_number": 1, "etag": "provider-secret-etag"}]},
        headers=_device_headers(pair),
    )
    assert completed.status_code == 200
    completed_body = completed.json()
    _assert_attachment_shape(completed_body)
    assert completed_body["id"] == str(attachment_id)
    assert completed_body["file_state"] == "QUARANTINED"
    assert "provider-secret-etag" not in completed.text
    assert len(import_api.dispatched) == 1

    submitted = import_api.client.post(
        f"/api/v1/device/import-jobs/{job_id}/submit",
        headers=_device_headers(pair),
    )
    assert submitted.status_code == 200
    _assert_submit_shape(submitted.json())
    assert submitted.json()["status"] == "SCANNING"

    read = import_api.client.get(
        f"/api/v1/device/import-jobs/{job_id}",
        headers=_device_headers(pair),
    )
    assert read.status_code == 200
    _assert_job_shape(read.json())
    assert read.json()["id"] == submitted.json()["id"]
    assert read.json()["status"] == submitted.json()["status"]
    assert pair.access_token not in read.text
