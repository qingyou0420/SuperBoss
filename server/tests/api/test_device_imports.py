"""HTTP contracts for device-scoped K3 intake and the OWNER import list."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import delete, event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from superboss.core.actors import Actor
from superboss.core.config import Settings
from superboss.main import create_app
from superboss.modules.audit.models import AuditLog
from superboss.modules.devices.models import (
    DeviceConnection,
    DeviceProjectGrant,
    DeviceScopeGrant,
    DeviceSession,
)
from superboss.modules.devices.service import DeviceService, DeviceTokenPair
from superboss.modules.files.models import File, FileState, FileUploadLifecycle, Upload
from superboss.modules.files.storage import ObjectMetadata
from superboss.modules.imports.models import ImportJob, ImportStatus
from superboss.modules.imports.schemas import ImportJobCreate
from superboss.modules.imports.service import ImportService
from superboss.modules.projects.models import Project, ProjectStatus
from superboss.modules.users.models import Role, User, UserStatus
from tests.files.storage import InMemoryObjectStorage
from tests.identity import LOCAL_TEST_PASSWORD, local_user

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
async def test_openapi_exposes_exactly_the_six_import_routes(import_api: ImportApiContext) -> None:
    expected = {
        "/api/v1/device/import-jobs": {"post"},
        "/api/v1/device/import-jobs/{job_id}/attachments/{attachment_id}/parts/{part_number}": {"post"},
        "/api/v1/device/import-jobs/{job_id}/attachments/{attachment_id}/complete": {"post"},
        "/api/v1/device/import-jobs/{job_id}/submit": {"post"},
        "/api/v1/device/import-jobs/{job_id}": {"get"},
        "/api/v1/owner/import-jobs": {"get"},
    }
    paths = import_api.client.get("/openapi.json").json()["paths"]
    actual = {
        path: set(operations) & {"get", "post", "put", "patch", "delete"}
        for path, operations in paths.items()
        if path.startswith("/api/v1/device/import-jobs")
        or path == "/api/v1/owner/import-jobs"
    }
    assert actual == expected


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
    upload = await db_session.get(Upload, UUID(attachment["upload_id"]))
    lifecycle = await db_session.get(FileUploadLifecycle, UUID(attachment["upload_id"]))
    assert file is not None and upload is not None and lifecycle is not None
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


@pytest.mark.asyncio
@pytest.mark.parametrize("header_value", [None, "bad key", "x" * 256])
async def test_create_requires_a_valid_idempotency_key_before_storage(
    import_api: ImportApiContext,
    db_session: AsyncSession,
    active_owner: User,
    header_value: str | None,
) -> None:
    project = Project(name=f"Import idempotency header {uuid4()}")
    db_session.add(project)
    await db_session.commit()
    pair = await _pair(import_api, db_session, active_owner.id, project.id)
    headers = _device_headers(pair)
    if header_value is not None:
        headers["Idempotency-Key"] = header_value
    before = _storage_calls(import_api.storage)

    response = import_api.client.post(
        "/api/v1/device/import-jobs",
        json=_body(project.id, "invalid-header"),
        headers=headers,
    )

    _assert_error(response, 422, "VALIDATION_ERROR")
    assert _storage_calls(import_api.storage) == before
    assert not await db_session.scalar(select(ImportJob.id))


@pytest.mark.asyncio
@pytest.mark.parametrize("project_case", ["existing_ungranted", "unknown"])
async def test_create_unknown_project_matches_existing_ungranted_denial(
    import_api: ImportApiContext,
    db_session: AsyncSession,
    active_owner: User,
    project_case: str,
) -> None:
    """A valid unknown UUID must be the same audited 403 as a real ungranted project."""
    granted_project = Project(name=f"Known create grant {project_case}")
    db_session.add(granted_project)
    await db_session.commit()
    pair = await _pair(
        import_api,
        db_session,
        active_owner.id,
        granted_project.id,
        name=f"Unknown-project-{project_case}",
    )
    if project_case == "existing_ungranted":
        target_project = Project(name=f"Known denied create {uuid4()}")
        db_session.add(target_project)
        await db_session.commit()
        target_project_id = target_project.id
        expected_audit_project_id: UUID | None = target_project.id
    else:
        target_project_id = uuid4()
        expected_audit_project_id = None
    request_id = uuid4()
    suffix = f"project-denial-{project_case}"
    before = _storage_calls(import_api.storage)

    response = import_api.client.post(
        "/api/v1/device/import-jobs",
        json=_body(target_project_id, suffix),
        headers={
            **_device_headers(pair, request_id=request_id),
            "Idempotency-Key": f"project-denial-{project_case}",
        },
    )

    _assert_error(response, 403, "IMPORT_CREATE_FORBIDDEN")
    assert _storage_calls(import_api.storage) == before
    assert not await db_session.scalar(select(ImportJob.id))
    assert not await db_session.scalar(select(File.id))
    assert not await db_session.scalar(select(Upload.id))
    assert not await db_session.scalar(select(FileUploadLifecycle.upload_id))
    audits = list(
        await db_session.scalars(
            select(AuditLog).where(
                AuditLog.request_id == request_id,
                AuditLog.action == "import.create",
                AuditLog.outcome == "DENIED",
            )
        )
    )
    assert len(audits) == 1
    assert audits[0].project_id == expected_audit_project_id
    serialized_audit = str(audits[0].metadata_json)
    assert str(target_project_id) not in serialized_audit
    assert suffix not in serialized_audit


@pytest.mark.asyncio
async def test_unknown_project_denial_audit_failure_is_safe_and_fail_closed(
    import_api: ImportApiContext,
    db_session: AsyncSession,
    active_owner: User,
) -> None:
    """An evidence failure must precede every business and provider side effect."""
    granted_project = Project(name="Unknown create audit fault grant")
    db_session.add(granted_project)
    await db_session.commit()
    pair = await _pair(import_api, db_session, active_owner.id, granted_project.id)
    unknown_project_id = uuid4()
    request_id = uuid4()
    before = _storage_calls(import_api.storage)

    def fail_denial(_mapper: object, _connection: object, target: AuditLog) -> None:
        if target.action == "import.create" and target.outcome == "DENIED":
            raise RuntimeError("unknown-project-audit-secret")

    event.listen(AuditLog, "before_insert", fail_denial)
    try:
        response = import_api.client.post(
            "/api/v1/device/import-jobs",
            json=_body(unknown_project_id, "unknown-audit-fault-manifest"),
            headers={
                **_device_headers(pair, request_id=request_id),
                "Idempotency-Key": "unknown-project-audit-fault",
            },
        )
    finally:
        event.remove(AuditLog, "before_insert", fail_denial)

    _assert_error(response, 500, "REQUEST_FAILED")
    assert "unknown-project-audit-secret" not in response.text
    assert str(unknown_project_id) not in response.text
    assert _storage_calls(import_api.storage) == before
    assert not await db_session.scalar(select(ImportJob.id))
    assert not await db_session.scalar(select(File.id))
    assert not await db_session.scalar(select(Upload.id))
    assert not await db_session.scalar(
        select(AuditLog.id).where(
            AuditLog.request_id == request_id,
            AuditLog.action == "import.create",
            AuditLog.outcome == "DENIED",
        )
    )


def _request_operation(
    context: ImportApiContext,
    pair: DeviceTokenPair,
    project_id: UUID,
    seeded: SeededImport | None,
    operation: str,
    request_id: UUID,
) -> httpx.Response:
    headers = _device_headers(pair, request_id=request_id)
    if operation == "create":
        return context.client.post(
            "/api/v1/device/import-jobs",
            json=_body(project_id, f"operation-{request_id}"),
            headers={**headers, "Idempotency-Key": f"operation-{request_id}"},
        )
    assert seeded is not None
    root = f"/api/v1/device/import-jobs/{seeded.job_id}"
    if operation == "part":
        return context.client.post(
            f"{root}/attachments/{seeded.attachment_id}/parts/1",
            headers=headers,
        )
    if operation == "complete":
        return context.client.post(
            f"{root}/attachments/{seeded.attachment_id}/complete",
            json={"parts": [{"part_number": 1, "etag": "scope-provider-secret"}]},
            headers=headers,
        )
    if operation == "submit":
        return context.client.post(f"{root}/submit", headers=headers)
    if operation == "read":
        return context.client.get(root, headers=headers)
    raise AssertionError(f"unknown operation: {operation}")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "required_scope", "expected_status"),
    [
        ("create", "imports:create", 201),
        ("part", "imports:upload", 200),
        ("complete", "imports:upload", 200),
        ("submit", "imports:submit", 200),
        ("read", "imports:read-own", 200),
    ],
)
async def test_each_device_operation_accepts_only_its_exact_live_scope(
    import_api: ImportApiContext,
    db_session: AsyncSession,
    active_owner: User,
    operation: str,
    required_scope: str,
    expected_status: int,
) -> None:
    project = Project(name=f"Exact import scope {operation}")
    db_session.add(project)
    await db_session.commit()
    pair = await _pair(import_api, db_session, active_owner.id, project.id)
    seeded = None
    if operation != "create":
        seeded = await _seed_import(
            import_api,
            db_session,
            pair,
            project.id,
            suffix=f"exact-{operation}",
        )
        if operation == "submit":
            file = await db_session.get(File, seeded.file_id)
            assert file is not None
            file.state = FileState.QUARANTINED
            await db_session.commit()
    await db_session.execute(
        delete(DeviceScopeGrant).where(
            DeviceScopeGrant.device_id == pair.device_id,
            DeviceScopeGrant.scope != required_scope,
        )
    )
    await db_session.commit()

    response = _request_operation(
        import_api,
        pair,
        project.id,
        seeded,
        operation,
        uuid4(),
    )

    assert response.status_code == expected_status
    if operation == "create":
        _assert_job_shape(response.json())
    elif operation == "part":
        assert set(response.json()) == {"url"}
    elif operation == "complete":
        _assert_attachment_shape(response.json())
    elif operation == "submit":
        _assert_submit_shape(response.json())
    else:
        _assert_job_shape(response.json())


@pytest.mark.asyncio
@pytest.mark.parametrize("job_case", ["scanning", "terminal"])
async def test_submit_only_scope_is_status_only_and_cannot_replace_read_own(
    import_api: ImportApiContext,
    db_session: AsyncSession,
    active_owner: User,
    job_case: str,
) -> None:
    """Submit can replay operation status but cannot disclose the read-scoped manifest."""
    project = Project(name=f"Submit-only response {job_case}")
    db_session.add(project)
    await db_session.commit()
    pair = await _pair(import_api, db_session, active_owner.id, project.id)
    suffix = f"submit-only-{job_case}-secret"
    seeded = await _seed_import(
        import_api,
        db_session,
        pair,
        project.id,
        suffix=suffix,
    )
    file = await db_session.get(File, seeded.file_id)
    assert file is not None
    if job_case == "scanning":
        file.state = FileState.QUARANTINED
        expected_status = "SCANNING"
        expected_new_audits = 1
        await db_session.commit()
    else:
        file.state = FileState.CLEAN
        file.scan_result = "CLEAN"
        await db_session.commit()
        await ImportService(_factory(db_session), import_api.storage).submit(
            Actor(
                "device",
                pair.device_id,
                None,
                frozenset({project.id}),
                DEVICE_SCOPES,
            ),
            seeded.job_id,
            request_id=uuid4(),
        )
        expected_status = "RECEIVED"
        expected_new_audits = 0

    await db_session.execute(
        delete(DeviceScopeGrant).where(
            DeviceScopeGrant.device_id == pair.device_id,
            DeviceScopeGrant.scope != "imports:submit",
        )
    )
    await db_session.commit()
    before_storage = _storage_calls(import_api.storage)
    before_audits = len(
        list(
            await db_session.scalars(
                select(AuditLog.id).where(
                    AuditLog.action == "import.submit",
                    AuditLog.object_id == seeded.job_id,
                    AuditLog.outcome == "SUCCESS",
                )
            )
        )
    )
    root = f"/api/v1/device/import-jobs/{seeded.job_id}"

    submit_responses = [
        import_api.client.post(f"{root}/submit", headers=_device_headers(pair))
        for _ in range(2)
    ]

    assert [response.status_code for response in submit_responses] == [200, 200]
    for response in submit_responses:
        _assert_submit_shape(response.json())
        assert response.json()["id"] == str(seeded.job_id)
        assert response.json()["status"] == expected_status
        assert suffix not in response.text
        assert str(seeded.attachment_id) not in response.text
        assert str(seeded.file_id) not in response.text
        assert str(seeded.upload_id) not in response.text
    after_audits = len(
        list(
            await db_session.scalars(
                select(AuditLog.id).where(
                    AuditLog.action == "import.submit",
                    AuditLog.object_id == seeded.job_id,
                    AuditLog.outcome == "SUCCESS",
                )
            )
        )
    )
    assert after_audits == before_audits + expected_new_audits
    assert _storage_calls(import_api.storage) == before_storage

    denied_read = import_api.client.get(root, headers=_device_headers(pair))
    _assert_error(denied_read, 403, "IMPORT_READ_FORBIDDEN")
    assert _storage_calls(import_api.storage) == before_storage

    db_session.add(
        DeviceScopeGrant(device_id=pair.device_id, scope="imports:read-own")
    )
    await db_session.commit()
    full_read = import_api.client.get(root, headers=_device_headers(pair))

    assert full_read.status_code == 200
    _assert_job_shape(full_read.json())
    assert full_read.json()["id"] == str(seeded.job_id)
    assert full_read.json()["status"] == expected_status
    assert suffix in full_read.text
    assert str(seeded.attachment_id) in full_read.text
    assert _storage_calls(import_api.storage) == before_storage


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "missing_scope", "error_code", "action"),
    [
        ("create", "imports:create", "IMPORT_CREATE_FORBIDDEN", "import.create"),
        ("part", "imports:upload", "IMPORT_UPLOAD_FORBIDDEN", "import.attachment.part_url"),
        (
            "complete",
            "imports:upload",
            "IMPORT_UPLOAD_FORBIDDEN",
            "import.attachment.complete",
        ),
        ("submit", "imports:submit", "IMPORT_SUBMIT_FORBIDDEN", "import.submit"),
        ("read", "imports:read-own", "IMPORT_READ_FORBIDDEN", "import.read"),
    ],
)
async def test_each_device_operation_rejects_a_missing_live_scope_with_safe_audit(
    import_api: ImportApiContext,
    db_session: AsyncSession,
    active_owner: User,
    operation: str,
    missing_scope: str,
    error_code: str,
    action: str,
) -> None:
    project = Project(name=f"Missing import scope {operation}")
    db_session.add(project)
    await db_session.commit()
    pair = await _pair(import_api, db_session, active_owner.id, project.id)
    seeded = None
    if operation != "create":
        seeded = await _seed_import(
            import_api,
            db_session,
            pair,
            project.id,
            suffix=f"missing-{operation}",
        )
    await db_session.execute(
        delete(DeviceScopeGrant).where(
            DeviceScopeGrant.device_id == pair.device_id,
            DeviceScopeGrant.scope == missing_scope,
        )
    )
    await db_session.commit()
    request_id = uuid4()
    before = _storage_calls(import_api.storage)

    response = _request_operation(
        import_api,
        pair,
        project.id,
        seeded,
        operation,
        request_id,
    )

    _assert_error(response, 403, error_code)
    assert _storage_calls(import_api.storage) == before
    denied = await db_session.scalar(
        select(AuditLog).where(
            AuditLog.request_id == request_id,
            AuditLog.action == action,
            AuditLog.outcome == "DENIED",
        )
    )
    assert denied is not None
    assert denied.actor_kind == "device" and denied.actor_id == pair.device_id
    serialized = str(denied.metadata_json).lower()
    assert error_code.lower() in serialized
    assert all(
        secret not in serialized
        for secret in (
            "scope-provider-secret",
            "modified-missing",
            "authorization",
            "multipart",
            "object_key",
            "etag",
            "token",
        )
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["create", "part", "complete", "submit"])
async def test_browser_posts_to_device_routes_require_csrf_before_actor_resolution(
    import_api: ImportApiContext,
    db_session: AsyncSession,
    active_owner: User,
    operation: str,
) -> None:
    project = Project(name=f"Import browser CSRF {operation}")
    db_session.add(project)
    await db_session.commit()
    pair = await _pair(import_api, db_session, active_owner.id, project.id)
    seeded = None
    if operation != "create":
        seeded = await _seed_import(
            import_api,
            db_session,
            pair,
            project.id,
            suffix=f"csrf-{operation}",
        )
    _login(import_api.client)
    before = _storage_calls(import_api.storage)
    request_id = uuid4()
    assert seeded is not None or operation == "create"
    if operation == "create":
        path = "/api/v1/device/import-jobs"
        body: dict[str, object] | None = _body(project.id, "csrf")
        headers = {"Idempotency-Key": "csrf-create", "X-Request-ID": str(request_id)}
    elif operation == "part":
        assert seeded is not None
        path = (
            f"/api/v1/device/import-jobs/{seeded.job_id}/attachments/"
            f"{seeded.attachment_id}/parts/1"
        )
        body = None
        headers = {"X-Request-ID": str(request_id)}
    elif operation == "complete":
        assert seeded is not None
        path = (
            f"/api/v1/device/import-jobs/{seeded.job_id}/attachments/"
            f"{seeded.attachment_id}/complete"
        )
        body = {"parts": [{"part_number": 1, "etag": "csrf-secret"}]}
        headers = {"X-Request-ID": str(request_id)}
    else:
        assert seeded is not None
        path = f"/api/v1/device/import-jobs/{seeded.job_id}/submit"
        body = None
        headers = {"X-Request-ID": str(request_id)}

    missing = import_api.client.post(path, json=body, headers=headers)
    wrong = import_api.client.post(
        path,
        json=body,
        headers={**headers, "X-CSRF-Token": "wrong"},
    )

    _assert_error(missing, 403, "CSRF_VALIDATION_FAILED")
    _assert_error(wrong, 403, "CSRF_VALIDATION_FAILED")
    assert _storage_calls(import_api.storage) == before
    assert not await db_session.scalar(
        select(AuditLog.id).where(
            AuditLog.request_id == request_id,
            AuditLog.action.like("import.%"),
        )
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("browser_role", [Role.OWNER, Role.STAFF])
@pytest.mark.parametrize(
    ("operation", "error_code", "action"),
    [
        ("create", "IMPORT_CREATE_FORBIDDEN", "import.create"),
        ("part", "IMPORT_UPLOAD_FORBIDDEN", "import.attachment.part_url"),
        ("complete", "IMPORT_UPLOAD_FORBIDDEN", "import.attachment.complete"),
        ("submit", "IMPORT_SUBMIT_FORBIDDEN", "import.submit"),
        ("read", "IMPORT_READ_FORBIDDEN", "import.read"),
    ],
)
async def test_browser_owner_and_staff_cannot_substitute_for_device_actor(
    import_api: ImportApiContext,
    db_session: AsyncSession,
    active_owner: User,
    browser_role: Role,
    operation: str,
    error_code: str,
    action: str,
) -> None:
    project = Project(name=f"Browser import denial {browser_role} {operation}")
    db_session.add(project)
    if browser_role == Role.STAFF:
        db_session.add(
            local_user("staff-1", display_name="Staff")
        )
    await db_session.commit()
    pair = await _pair(import_api, db_session, active_owner.id, project.id)
    seeded = None
    if operation != "create":
        seeded = await _seed_import(
            import_api,
            db_session,
            pair,
            project.id,
            suffix=f"browser-{browser_role}-{operation}",
        )
    _login(import_api.client, "owner-code" if browser_role == Role.OWNER else "staff-code")
    request_id = uuid4()
    before = _storage_calls(import_api.storage)

    headers = {"X-Request-ID": str(request_id)}
    if operation != "read":
        headers.update(_csrf(import_api.client))
    if operation == "create":
        response = import_api.client.post(
            "/api/v1/device/import-jobs",
            json=_body(project.id, "browser-secret"),
            headers={**headers, "Idempotency-Key": "browser-create"},
        )
    else:
        assert seeded is not None
        root = f"/api/v1/device/import-jobs/{seeded.job_id}"
        if operation == "part":
            response = import_api.client.post(
                f"{root}/attachments/{seeded.attachment_id}/parts/1",
                headers=headers,
            )
        elif operation == "complete":
            response = import_api.client.post(
                f"{root}/attachments/{seeded.attachment_id}/complete",
                json={"parts": [{"part_number": 1, "etag": "browser-secret-etag"}]},
                headers=headers,
            )
        elif operation == "submit":
            response = import_api.client.post(f"{root}/submit", headers=headers)
        else:
            response = import_api.client.get(root, headers=headers)

    _assert_error(response, 403, error_code)
    assert _storage_calls(import_api.storage) == before
    denied = await db_session.scalar(
        select(AuditLog).where(
            AuditLog.request_id == request_id,
            AuditLog.action == action,
            AuditLog.outcome == "DENIED",
        )
    )
    assert denied is not None and denied.actor_kind == "user"
    assert denied.metadata_json.get("actor_role") == browser_role.value
    serialized = str(denied.metadata_json).lower()
    assert all(
        secret not in serialized
        for secret in ("browser-secret", "authorization", "cookie", "multipart", "etag")
    )


@pytest.mark.asyncio
async def test_browser_cookie_takes_priority_over_device_bearer_on_device_post(
    import_api: ImportApiContext,
    db_session: AsyncSession,
    active_owner: User,
) -> None:
    project = Project(name="Browser cookie credential priority")
    db_session.add(project)
    await db_session.commit()
    pair = await _pair(import_api, db_session, active_owner.id, project.id)
    _login(import_api.client)
    authorization = {"Authorization": f"Bearer {pair.access_token}"}
    body = _body(project.id, "credential-priority")
    before = _storage_calls(import_api.storage)

    missing_csrf = import_api.client.post(
        "/api/v1/device/import-jobs",
        json=body,
        headers={**authorization, "Idempotency-Key": "priority-missing"},
    )
    request_id = uuid4()
    actor_denied = import_api.client.post(
        "/api/v1/device/import-jobs",
        json=body,
        headers={
            **authorization,
            **_csrf(import_api.client),
            "Idempotency-Key": "priority-valid",
            "X-Request-ID": str(request_id),
        },
    )

    _assert_error(missing_csrf, 403, "CSRF_VALIDATION_FAILED")
    _assert_error(actor_denied, 403, "IMPORT_CREATE_FORBIDDEN")
    assert _storage_calls(import_api.storage) == before
    denied = await db_session.scalar(
        select(AuditLog).where(
            AuditLog.request_id == request_id,
            AuditLog.action == "import.create",
            AuditLog.outcome == "DENIED",
        )
    )
    assert denied is not None
    assert denied.actor_kind == "user" and denied.actor_id == active_owner.id


@pytest.mark.asyncio
async def test_anonymous_and_invalid_bearer_device_posts_are_not_csrf_exempt(
    import_api: ImportApiContext,
    db_session: AsyncSession,
) -> None:
    project = Project(name="Import route authentication ordering")
    db_session.add(project)
    await db_session.commit()
    body = _body(project.id, "anonymous")

    anonymous = import_api.client.post(
        "/api/v1/device/import-jobs",
        json=body,
        headers={"Idempotency-Key": "anonymous"},
    )
    invalid = import_api.client.post(
        "/api/v1/device/import-jobs",
        json=body,
        headers={
            "Authorization": "Bearer invalid-device-secret",
            "Idempotency-Key": "invalid-bearer",
        },
    )

    _assert_error(anonymous, 401, "AUTHENTICATION_REQUIRED")
    _assert_error(invalid, 401, "AUTHENTICATION_REQUIRED")
    assert _storage_calls(import_api.storage) == (0,) * 9


def test_near_miss_import_path_is_not_an_authenticated_write_prefix(
    import_api: ImportApiContext,
) -> None:
    response = import_api.client.post("/api/v1/device/import-jobsevil", json={})

    _assert_error(response, 403, "CSRF_VALIDATION_FAILED")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "malformed_url",
    [
        "https://storage.invalid/part\rheader-secret",
        "https://storage.invalid/part\nheader-secret",
        "https://storage.invalid/part\x00nul-secret",
        "https://storage.invalid/part\x7fdel-secret",
    ],
)
async def test_part_url_rejects_provider_control_characters_with_safe_500(
    import_api: ImportApiContext,
    db_session: AsyncSession,
    active_owner: User,
    malformed_url: str,
) -> None:
    project = Project(name=f"Unsafe provider URL {uuid4()}")
    db_session.add(project)
    await db_session.commit()
    pair = await _pair(import_api, db_session, active_owner.id, project.id)
    seeded = await _seed_import(
        import_api,
        db_session,
        pair,
        project.id,
        suffix=f"unsafe-url-{uuid4()}",
    )
    import_api.storage.part_url_override = malformed_url

    response = import_api.client.post(
        f"/api/v1/device/import-jobs/{seeded.job_id}/attachments/"
        f"{seeded.attachment_id}/parts/1",
        headers=_device_headers(pair),
    )

    _assert_error(response, 500, "REQUEST_FAILED")
    assert malformed_url not in response.text
    assert all(secret not in response.text for secret in ("header-secret", "nul-secret", "del-secret"))


@pytest.mark.asyncio
async def test_foreign_unknown_and_mixed_import_ids_share_safe_not_found_boundaries(
    import_api: ImportApiContext,
    db_session: AsyncSession,
    active_owner: User,
) -> None:
    project = Project(name="Import ID non-disclosure")
    db_session.add(project)
    await db_session.commit()
    first_pair = await _pair(
        import_api,
        db_session,
        active_owner.id,
        project.id,
        name="First-PC",
    )
    second_pair = await _pair(
        import_api,
        db_session,
        active_owner.id,
        project.id,
        name="Second-PC",
    )
    first = await _seed_import(
        import_api,
        db_session,
        first_pair,
        project.id,
        suffix="first-job",
    )
    same_device_other = await _seed_import(
        import_api,
        db_session,
        first_pair,
        project.id,
        suffix="same-device-other-job",
    )
    await _seed_import(
        import_api,
        db_session,
        second_pair,
        project.id,
        suffix="second-device-job",
    )
    unknown_job = uuid4()
    unknown_attachment = uuid4()
    request_ids = [uuid4() for _ in range(8)]
    first_root = f"/api/v1/device/import-jobs/{first.job_id}"
    cases = [
        (
            "job",
            "get",
            first_root,
            None,
            _device_headers(second_pair, request_id=request_ids[0]),
            "IMPORT_JOB_NOT_FOUND",
            "import.read",
        ),
        (
            "job",
            "get",
            f"/api/v1/device/import-jobs/{unknown_job}",
            None,
            _device_headers(first_pair, request_id=request_ids[1]),
            "IMPORT_JOB_NOT_FOUND",
            "import.read",
        ),
        (
            "job",
            "post",
            f"{first_root}/submit",
            None,
            _device_headers(second_pair, request_id=request_ids[2]),
            "IMPORT_JOB_NOT_FOUND",
            "import.submit",
        ),
        (
            "job",
            "post",
            f"/api/v1/device/import-jobs/{unknown_job}/submit",
            None,
            _device_headers(first_pair, request_id=request_ids[3]),
            "IMPORT_JOB_NOT_FOUND",
            "import.submit",
        ),
        (
            "attachment",
            "post",
            f"{first_root}/attachments/{first.attachment_id}/parts/1",
            None,
            _device_headers(second_pair, request_id=request_ids[4]),
            "IMPORT_ATTACHMENT_NOT_FOUND",
            "import.attachment.part_url",
        ),
        (
            "attachment",
            "post",
            f"{first_root}/attachments/{same_device_other.attachment_id}/parts/1",
            None,
            _device_headers(first_pair, request_id=request_ids[5]),
            "IMPORT_ATTACHMENT_NOT_FOUND",
            "import.attachment.part_url",
        ),
        (
            "attachment",
            "post",
            (
                f"/api/v1/device/import-jobs/{unknown_job}/attachments/"
                f"{first.attachment_id}/complete"
            ),
            {"parts": [{"part_number": 1, "etag": "mixed-provider-secret"}]},
            _device_headers(first_pair, request_id=request_ids[6]),
            "IMPORT_ATTACHMENT_NOT_FOUND",
            "import.attachment.complete",
        ),
        (
            "attachment",
            "post",
            f"{first_root}/attachments/{unknown_attachment}/complete",
            {"parts": [{"part_number": 1, "etag": "unknown-provider-secret"}]},
            _device_headers(first_pair, request_id=request_ids[7]),
            "IMPORT_ATTACHMENT_NOT_FOUND",
            "import.attachment.complete",
        ),
    ]
    before = _storage_calls(import_api.storage)
    signatures: dict[str, set[tuple[int, str, str]]] = {"job": set(), "attachment": set()}

    for group, method, path, body, headers, error_code, _action in cases:
        response = import_api.client.request(method, path, json=body, headers=headers)
        _assert_error(response, 404, error_code)
        error = response.json()["error"]
        signatures[group].add((response.status_code, error["code"], error["message"]))

    assert signatures == {
        "job": {(404, "IMPORT_JOB_NOT_FOUND", "Import job not found")},
        "attachment": {
            (404, "IMPORT_ATTACHMENT_NOT_FOUND", "Import attachment not found")
        },
    }
    assert _storage_calls(import_api.storage) == before
    denied = list(
        await db_session.scalars(
            select(AuditLog).where(
                AuditLog.request_id.in_(request_ids),
                AuditLog.outcome == "DENIED",
            )
        )
    )
    assert len(denied) == len(cases)
    assert {(item.action, item.request_id) for item in denied} == {
        (action, request_id)
        for (*_unused, action), request_id in zip(cases, request_ids, strict=True)
    }
    serialized = str([item.metadata_json for item in denied]).lower()
    assert all(
        secret not in serialized
        for secret in (
            "mixed-provider-secret",
            "unknown-provider-secret",
            "authorization",
            "object_key",
            "multipart",
            "etag",
            "token",
        )
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path_kind", "path_suffix"),
    [
        ("read", "/api/v1/device/import-jobs/not-a-uuid"),
        ("submit", "/api/v1/device/import-jobs/not-a-uuid/submit"),
        ("part", "part-zero"),
        ("part", "part-too-large"),
        ("attachment", "bad-attachment"),
    ],
)
async def test_malformed_ids_and_part_numbers_use_bounded_validation_errors_without_io(
    import_api: ImportApiContext,
    db_session: AsyncSession,
    active_owner: User,
    path_kind: str,
    path_suffix: str,
) -> None:
    project = Project(name=f"Malformed import path {path_kind} {path_suffix}")
    db_session.add(project)
    await db_session.commit()
    pair = await _pair(import_api, db_session, active_owner.id, project.id)
    seeded = await _seed_import(
        import_api,
        db_session,
        pair,
        project.id,
        suffix=f"malformed-{uuid4()}",
    )
    if path_suffix == "part-zero":
        path = (
            f"/api/v1/device/import-jobs/{seeded.job_id}/attachments/"
            f"{seeded.attachment_id}/parts/0"
        )
    elif path_suffix == "part-too-large":
        path = (
            f"/api/v1/device/import-jobs/{seeded.job_id}/attachments/"
            f"{seeded.attachment_id}/parts/10001"
        )
    elif path_suffix == "bad-attachment":
        path = (
            f"/api/v1/device/import-jobs/{seeded.job_id}/attachments/"
            "not-a-uuid/complete"
        )
    else:
        path = path_suffix
    before = _storage_calls(import_api.storage)

    response = import_api.client.request(
        "GET" if path_kind == "read" else "POST",
        path,
        json={"parts": [{"part_number": 1, "etag": "validation-secret"}]}
        if path_kind == "attachment"
        else None,
        headers=_device_headers(pair),
    )

    _assert_error(response, 422, "VALIDATION_ERROR")
    assert _storage_calls(import_api.storage) == before
    assert "validation-secret" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("live_change", "expected_status", "expected_code", "authenticated"),
    [
        ("grant_removed", 403, "IMPORT_CREATE_FORBIDDEN", True),
        ("project_archived", 403, "IMPORT_CREATE_FORBIDDEN", True),
        ("device_revoked", 401, "AUTHENTICATION_REQUIRED", False),
        ("session_revoked", 401, "AUTHENTICATION_REQUIRED", False),
        ("owner_disabled", 401, "AUTHENTICATION_REQUIRED", False),
        ("owner_demoted", 401, "AUTHENTICATION_REQUIRED", False),
    ],
)
async def test_device_imports_recheck_live_grants_and_credential_owners(
    import_api: ImportApiContext,
    db_session: AsyncSession,
    active_owner: User,
    live_change: str,
    expected_status: int,
    expected_code: str,
    authenticated: bool,
) -> None:
    project = Project(name=f"Live import auth {live_change}")
    db_session.add(project)
    await db_session.commit()
    pair = await _pair(import_api, db_session, active_owner.id, project.id)
    now = datetime.now(UTC)
    if live_change == "grant_removed":
        await db_session.execute(
            delete(DeviceProjectGrant).where(
                DeviceProjectGrant.device_id == pair.device_id,
                DeviceProjectGrant.project_id == project.id,
            )
        )
    elif live_change == "project_archived":
        project.status = ProjectStatus.ARCHIVED
    elif live_change == "device_revoked":
        device = await db_session.get(DeviceConnection, pair.device_id)
        assert device is not None
        device.revoked_at = now
    elif live_change == "session_revoked":
        device_session = await db_session.scalar(
            select(DeviceSession).where(DeviceSession.device_id == pair.device_id)
        )
        assert device_session is not None
        device_session.revoked_at = now
    elif live_change == "owner_disabled":
        active_owner.status = UserStatus.DISABLED
    elif live_change == "owner_demoted":
        active_owner.role = Role.STAFF
    else:
        raise AssertionError(live_change)
    await db_session.commit()
    request_id = uuid4()
    before = _storage_calls(import_api.storage)

    response = import_api.client.post(
        "/api/v1/device/import-jobs",
        json=_body(project.id, f"live-{live_change}-secret"),
        headers={
            **_device_headers(pair, request_id=request_id),
            "Idempotency-Key": f"live-{live_change}",
        },
    )

    _assert_error(response, expected_status, expected_code)
    assert _storage_calls(import_api.storage) == before
    import_denial = await db_session.scalar(
        select(AuditLog).where(
            AuditLog.request_id == request_id,
            AuditLog.action == "import.create",
            AuditLog.outcome == "DENIED",
        )
    )
    if authenticated:
        assert import_denial is not None
        serialized = str(import_denial.metadata_json).lower()
        assert "live-" not in serialized and "secret" not in serialized
    else:
        assert import_denial is None
    assert pair.access_token not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("live_change", ["grant_removed", "project_archived"])
@pytest.mark.parametrize(
    ("operation", "error_code", "action"),
    [
        ("part", "IMPORT_UPLOAD_FORBIDDEN", "import.attachment.part_url"),
        ("complete", "IMPORT_UPLOAD_FORBIDDEN", "import.attachment.complete"),
        ("submit", "IMPORT_SUBMIT_FORBIDDEN", "import.submit"),
        ("read", "IMPORT_READ_FORBIDDEN", "import.read"),
    ],
)
async def test_existing_job_routes_recheck_live_project_grants(
    import_api: ImportApiContext,
    db_session: AsyncSession,
    active_owner: User,
    live_change: str,
    operation: str,
    error_code: str,
    action: str,
) -> None:
    project = Project(name=f"Existing import live grant {live_change} {operation}")
    db_session.add(project)
    await db_session.commit()
    pair = await _pair(import_api, db_session, active_owner.id, project.id)
    seeded = await _seed_import(
        import_api,
        db_session,
        pair,
        project.id,
        suffix=f"live-existing-{live_change}-{operation}",
    )
    if live_change == "grant_removed":
        await db_session.execute(
            delete(DeviceProjectGrant).where(
                DeviceProjectGrant.device_id == pair.device_id,
                DeviceProjectGrant.project_id == project.id,
            )
        )
    else:
        project.status = ProjectStatus.ARCHIVED
    await db_session.commit()
    request_id = uuid4()
    before = _storage_calls(import_api.storage)

    response = _request_operation(
        import_api,
        pair,
        project.id,
        seeded,
        operation,
        request_id,
    )

    _assert_error(response, 403, error_code)
    assert _storage_calls(import_api.storage) == before
    denied = await db_session.scalar(
        select(AuditLog).where(
            AuditLog.request_id == request_id,
            AuditLog.action == action,
            AuditLog.outcome == "DENIED",
        )
    )
    assert denied is not None
    assert denied.actor_kind == "device" and denied.actor_id == pair.device_id
    serialized = str(denied.metadata_json).lower()
    assert all(
        secret not in serialized
        for secret in ("authorization", "multipart", "object_key", "etag", "token")
    )


@pytest.mark.asyncio
async def test_device_get_recovers_a_missed_scan_callback_without_provider_io(
    import_api: ImportApiContext,
    db_session: AsyncSession,
    active_owner: User,
) -> None:
    project = Project(name="Import GET callback recovery")
    db_session.add(project)
    await db_session.commit()
    pair = await _pair(import_api, db_session, active_owner.id, project.id)
    seeded = await _seed_import(
        import_api,
        db_session,
        pair,
        project.id,
        suffix="read-reconcile",
    )
    now = datetime.now(UTC)
    job = await db_session.get(ImportJob, seeded.job_id)
    file = await db_session.get(File, seeded.file_id)
    assert job is not None and file is not None
    job.status = ImportStatus.SCANNING
    job.submitted_at = now
    job.updated_at = now
    file.state = FileState.CLEAN
    file.scan_result = "CLEAN"
    await db_session.commit()
    before = _storage_calls(import_api.storage)

    responses = [
        import_api.client.get(
            f"/api/v1/device/import-jobs/{seeded.job_id}",
            headers=_device_headers(pair),
        )
        for _ in range(2)
    ]

    assert [response.status_code for response in responses] == [200, 200]
    for response in responses:
        _assert_job_shape(response.json())
        assert response.json()["status"] == "RECEIVED"
    assert _storage_calls(import_api.storage) == before
    terminal_events = list(
        await db_session.scalars(
            select(AuditLog).where(
                AuditLog.action == "import.reconcile",
                AuditLog.object_id == seeded.job_id,
                AuditLog.outcome == "SUCCESS",
            )
        )
    )
    assert len(terminal_events) == 1
    assert terminal_events[0].actor_kind == "system"
    assert terminal_events[0].actor_id is None


def _assert_owner_summary_shape(value: dict[str, object]) -> None:
    assert set(value) == OWNER_SUMMARY_KEYS
    UUID(str(value["id"]))
    UUID(str(value["project_id"]))
    assert isinstance(value["local_task_id"], str) and len(value["local_task_id"]) <= 255
    external = value["external_document_reference"]
    assert external is None or isinstance(external, str) and len(external) <= 1024
    assert isinstance(value["model_label"], str) and 1 <= len(value["model_label"]) <= 128
    assert value["status"] in {"UPLOADING", "SCANNING", "RECEIVED", "REJECTED", "CONFLICT"}
    result = value["result_code"]
    assert result is None or isinstance(result, str) and 1 <= len(result) <= 64
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


@pytest.mark.asyncio
async def test_owner_list_is_bounded_newest_first_and_uses_safe_summary_models(
    import_api: ImportApiContext,
    db_session: AsyncSession,
    active_owner: User,
) -> None:
    project = Project(name="Bounded newest imports")
    db_session.add(project)
    await db_session.commit()
    pair = await _pair(import_api, db_session, active_owner.id, project.id)
    seeded = [
        await _seed_import(
            import_api,
            db_session,
            pair,
            project.id,
            suffix=f"ordered-{index}",
        )
        for index in range(3)
    ]
    reference = datetime.now(UTC) - timedelta(minutes=10)
    for index, item in enumerate(seeded):
        job = await db_session.get(ImportJob, item.job_id)
        assert job is not None
        timestamp = reference + timedelta(minutes=min(index, 1))
        job.created_at = timestamp
        job.updated_at = timestamp
    await db_session.commit()
    _login(import_api.client)

    response = import_api.client.get(
        "/api/v1/owner/import-jobs",
        params={"limit": 2},
        headers={"Authorization": f"Bearer {pair.access_token}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list) and len(body) == 2
    tied_newest = sorted(
        (seeded[1].job_id, seeded[2].job_id),
        reverse=True,
    )
    assert [item["id"] for item in body] == [str(job_id) for job_id in tied_newest]
    for item in body:
        assert isinstance(item, dict)
        _assert_owner_summary_shape(item)
        assert "k3_result" not in item
    assert pair.access_token not in response.text
    for item in seeded:
        file = await db_session.get(File, item.file_id)
        upload = await db_session.get(Upload, item.upload_id)
        assert file is not None and upload is not None and upload.multipart_id is not None
        assert file.object_key not in response.text
        assert upload.multipart_id not in response.text

    for invalid_limit in (0, 101):
        invalid = import_api.client.get(
            "/api/v1/owner/import-jobs",
            params={"limit": invalid_limit},
        )
        _assert_error(invalid, 422, "VALIDATION_ERROR")


@pytest.mark.asyncio
async def test_owner_list_rejects_anonymous_staff_and_device_with_safe_actor_audits(
    import_api: ImportApiContext,
    db_session: AsyncSession,
    active_owner: User,
) -> None:
    project = Project(name="Owner import list actor matrix")
    staff = local_user("staff-1", display_name="Staff")
    db_session.add_all([project, staff])
    await db_session.commit()
    pair = await _pair(import_api, db_session, active_owner.id, project.id)
    request_ids = [uuid4(), uuid4(), uuid4()]

    anonymous = import_api.client.get(
        "/api/v1/owner/import-jobs",
        headers={"X-Request-ID": str(request_ids[0])},
    )
    _login(import_api.client, "staff-code")
    staff_denied = import_api.client.get(
        "/api/v1/owner/import-jobs",
        headers={"X-Request-ID": str(request_ids[1])},
    )
    import_api.client.cookies.clear()
    device_denied = import_api.client.get(
        "/api/v1/owner/import-jobs",
        headers=_device_headers(pair, request_id=request_ids[2]),
    )

    _assert_error(anonymous, 401, "AUTHENTICATION_REQUIRED")
    _assert_error(staff_denied, 403, "OWNER_REQUIRED")
    _assert_error(device_denied, 403, "OWNER_REQUIRED")
    assert not await db_session.scalar(
        select(AuditLog.id).where(AuditLog.request_id == request_ids[0])
    )
    denied = list(
        await db_session.scalars(
            select(AuditLog).where(
                AuditLog.request_id.in_(request_ids[1:]),
                AuditLog.action == "import.list",
                AuditLog.outcome == "DENIED",
            )
        )
    )
    assert {(item.actor_kind, item.actor_id) for item in denied} == {
        ("user", staff.id),
        ("device", pair.device_id),
    }
    serialized = str([item.metadata_json for item in denied]).lower()
    assert all(
        secret not in serialized
        for secret in (pair.access_token.lower(), "authorization", "token", "hash", "cookie")
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("owner_change", ["disabled", "demoted"])
async def test_owner_list_rechecks_the_live_browser_owner(
    import_api: ImportApiContext,
    db_session: AsyncSession,
    active_owner: User,
    owner_change: str,
) -> None:
    await db_session.commit()
    _login(import_api.client)
    if owner_change == "disabled":
        active_owner.status = UserStatus.DISABLED
    else:
        active_owner.role = Role.STAFF
    await db_session.commit()

    response = import_api.client.get("/api/v1/owner/import-jobs")

    _assert_error(response, 401, "AUTHENTICATION_REQUIRED")


@pytest.mark.asyncio
async def test_import_read_denial_audit_failure_returns_safe_500_before_io(
    import_api: ImportApiContext,
    db_session: AsyncSession,
    active_owner: User,
) -> None:
    project = Project(name="Import read denial audit fault")
    db_session.add(project)
    await db_session.commit()
    pair = await _pair(import_api, db_session, active_owner.id, project.id)
    seeded = await _seed_import(
        import_api,
        db_session,
        pair,
        project.id,
        suffix="read-audit-fault-secret",
    )
    await db_session.execute(
        delete(DeviceScopeGrant).where(
            DeviceScopeGrant.device_id == pair.device_id,
            DeviceScopeGrant.scope == "imports:read-own",
        )
    )
    await db_session.commit()
    request_id = uuid4()
    before = _storage_calls(import_api.storage)

    def fail_denial(_mapper: object, _connection: object, target: AuditLog) -> None:
        if target.action == "import.read" and target.outcome == "DENIED":
            raise RuntimeError("audit provider secret")

    event.listen(AuditLog, "before_insert", fail_denial)
    try:
        response = import_api.client.get(
            f"/api/v1/device/import-jobs/{seeded.job_id}",
            headers=_device_headers(pair, request_id=request_id),
        )
    finally:
        event.remove(AuditLog, "before_insert", fail_denial)

    _assert_error(response, 500, "REQUEST_FAILED")
    assert "audit provider secret" not in response.text
    assert _storage_calls(import_api.storage) == before
    assert not await db_session.scalar(
        select(AuditLog.id).where(
            AuditLog.request_id == request_id,
            AuditLog.action == "import.read",
        )
    )


@pytest.mark.asyncio
async def test_create_success_audit_failure_returns_safe_500_without_import_row(
    import_api: ImportApiContext,
    db_session: AsyncSession,
    active_owner: User,
) -> None:
    project = Project(name="Import create success audit fault")
    db_session.add(project)
    await db_session.commit()
    pair = await _pair(import_api, db_session, active_owner.id, project.id)
    request_id = uuid4()

    def fail_success(_mapper: object, _connection: object, target: AuditLog) -> None:
        if target.action == "import.create" and target.outcome == "SUCCESS":
            raise RuntimeError("audit persistence secret")

    event.listen(AuditLog, "before_insert", fail_success)
    try:
        response = import_api.client.post(
            "/api/v1/device/import-jobs",
            json=_body(project.id, "create-audit-fault-secret"),
            headers={
                **_device_headers(pair, request_id=request_id),
                "Idempotency-Key": "http-create-audit-fault",
            },
        )
    finally:
        event.remove(AuditLog, "before_insert", fail_success)

    _assert_error(response, 500, "REQUEST_FAILED")
    assert "audit persistence secret" not in response.text
    assert "create-audit-fault-secret" not in response.text
    assert not await db_session.scalar(
        select(ImportJob.id).where(
            ImportJob.device_id == pair.device_id,
            ImportJob.idempotency_key == "http-create-audit-fault",
        )
    )


@pytest.mark.asyncio
async def test_generic_file_and_project_routes_remain_closed_to_devices(
    import_api: ImportApiContext,
    db_session: AsyncSession,
    active_owner: User,
) -> None:
    project = Project(name="Generic routes stay browser-only")
    db_session.add(project)
    await db_session.commit()
    pair = await _pair(import_api, db_session, active_owner.id, project.id)
    headers = _device_headers(pair)
    before = _storage_calls(import_api.storage)

    projects = import_api.client.get("/api/v1/projects", headers=headers)
    upload = import_api.client.post(
        "/api/v1/files/uploads",
        json={
            "project_id": str(project.id),
            "filename": "generic-device.pdf",
            "size_bytes": 1,
            "sha256": "b" * 64,
            "category": "docs",
            "file_date": "2026-08-10",
            "content_type": "application/pdf",
        },
        headers={**headers, "Idempotency-Key": "generic-device-upload"},
    )

    _assert_error(projects, 403, "PROJECT_FORBIDDEN")
    _assert_error(upload, 403, "PROJECT_FORBIDDEN")
    assert _storage_calls(import_api.storage) == before
