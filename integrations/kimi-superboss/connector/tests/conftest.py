from __future__ import annotations

import hashlib
import importlib
import json
from collections.abc import Callable, Iterator
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import keyring
import platformdirs
import pytest
import respx
from keyring.backend import KeyringBackend
from typer.testing import CliRunner

ORIGIN = "https://nightforest.com"
SERVICE = f"SuperBoss/KimiConnector/{ORIGIN}"
USERNAME = "device_refresh"
TIMESTAMP = "2026-08-10T09:54:00Z"


class MemoryKeyring(KeyringBackend):
    priority = 1

    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}
        self.get_calls: list[tuple[str, str]] = []
        self.set_calls: list[tuple[str, str, str]] = []
        self.delete_calls: list[tuple[str, str]] = []
        self.write_error: Exception | None = None

    def get_password(self, service: str, username: str) -> str | None:
        self.get_calls.append((service, username))
        return self.values.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.set_calls.append((service, username, password))
        if self.write_error is not None:
            raise self.write_error
        self.values[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self.delete_calls.append((service, username))
        self.values.pop((service, username), None)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def memory_keyring() -> Iterator[MemoryKeyring]:
    backend = MemoryKeyring()
    previous = keyring.get_keyring()
    keyring.set_keyring(backend)
    try:
        yield backend
    finally:
        keyring.set_keyring(previous)


@pytest.fixture
def state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    state = tmp_path / "connector-state"

    def fake_user_state_dir(*_args: object, **_kwargs: object) -> str:
        return str(state)

    monkeypatch.setattr(platformdirs, "user_state_dir", fake_user_state_dir)
    return state


def load_app(monkeypatch: pytest.MonkeyPatch, state_dir: Path) -> Any:
    """Load the wished-for CLI only inside a test, keeping RED as a test failure."""
    try:
        module = importlib.import_module("superboss_connector.cli")
    except ModuleNotFoundError as error:
        if error.name == "superboss_connector":
            pytest.fail(
                "Task 11 RED: superboss_connector package is not implemented",
                pytrace=False,
            )
        raise

    def fake_user_state_dir(*_args: object, **_kwargs: object) -> str:
        return str(state_dir)

    for module_name in (
        "superboss_connector.config",
        "superboss_connector.outbox",
        "superboss_connector.cli",
    ):
        loaded = importlib.import_module(module_name)
        if hasattr(loaded, "user_state_dir"):
            monkeypatch.setattr(loaded, "user_state_dir", fake_user_state_dir)
    return module.app


def token_payload(*, access: str, refresh: str) -> dict[str, str]:
    return {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "bearer",
        "expires_at": "2026-08-10T11:54:00Z",
        "refresh_expires_at": "2026-08-24T09:54:00Z",
    }


def write_manifest(
    directory: Path,
    *,
    content: bytes = b'{"k3":"result"}',
    idempotency_key: str = "kimi-task-001",
    project_id: UUID | None = None,
    filename: str = "k3-result.json",
) -> tuple[Path, Path, dict[str, Any]]:
    directory.mkdir(parents=True, exist_ok=True)
    attachment = directory / filename
    attachment.write_bytes(content)
    payload: dict[str, Any] = {
        "idempotency_key": idempotency_key,
        "project_id": str(project_id or uuid4()),
        "local_task_id": "kimi-local-001",
        "external_document_reference": "customer-proposal-001",
        "base_sha256": None,
        "k3_result": {
            "model_label": "K3",
            "processed_at": "2026-08-10T17:54:00+08:00",
            "modification_details": ["Updated payment milestones"],
            "knowledge_points": ["Customer requires staged acceptance"],
            "risks": ["Final legal review is pending"],
            "suggested_title": "Customer proposal revision",
            "suggested_tags": ["customer", "proposal"],
        },
        "attachments": [
            {
                "kind": "K3_RAW",
                "path": filename,
                "content_type": "application/json",
            }
        ],
    }
    manifest = directory / "manifest.json"
    manifest.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest, attachment, payload


def rewrite_manifest(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def expected_server_manifest(payload: dict[str, Any], attachment: Path) -> dict[str, Any]:
    result: dict[str, Any] = deepcopy(
        {
            key: payload[key]
            for key in (
                "project_id",
                "local_task_id",
                "external_document_reference",
                "base_sha256",
                "k3_result",
            )
        }
    )
    processed_at = datetime.fromisoformat(result["k3_result"]["processed_at"])
    result["k3_result"]["processed_at"] = (
        processed_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
    )
    result["attachments"] = [
        {
            "kind": "K3_RAW",
            "filename": attachment.name,
            "size_bytes": attachment.stat().st_size,
            "sha256": hashlib.sha256(attachment.read_bytes()).hexdigest(),
            "content_type": "application/json",
        }
    ]
    return result


def attachment_payload(
    *,
    attachment_id: UUID,
    file_id: UUID,
    upload_id: UUID,
    state: str = "UPLOADING",
) -> dict[str, str]:
    return {
        "id": str(attachment_id),
        "file_id": str(file_id),
        "upload_id": str(upload_id),
        "kind": "K3_RAW",
        "file_state": state,
    }


def job_payload(
    server_manifest: dict[str, Any],
    *,
    job_id: UUID,
    attachment_id: UUID,
    file_id: UUID,
    upload_id: UUID,
    status: str = "UPLOADING",
) -> dict[str, Any]:
    file_state = "UPLOADING" if status == "UPLOADING" else "QUARANTINED"
    return {
        "id": str(job_id),
        "project_id": server_manifest["project_id"],
        "local_task_id": server_manifest["local_task_id"],
        "external_document_reference": server_manifest["external_document_reference"],
        "base_sha256": server_manifest["base_sha256"],
        "status": status,
        "result_code": None,
        "k3_result": server_manifest["k3_result"],
        "submitted_at": None,
        "created_at": TIMESTAMP,
        "updated_at": TIMESTAMP,
        "attachments": [
            attachment_payload(
                attachment_id=attachment_id,
                file_id=file_id,
                upload_id=upload_id,
                state=file_state,
            )
        ],
    }


def error_payload(code: str) -> dict[str, dict[str, str]]:
    return {
        "error": {
            "code": code,
            "message": "Request rejected",
            "request_id": str(uuid4()),
        }
    }


ManifestMutation = Callable[[dict[str, Any], Path], None]


@dataclass
class ResponseLossFlow:
    manifest: Path
    attachment: Path
    idempotency_key: str
    job_id: UUID
    attachment_id: UUID
    refresh_route: Any
    create_route: Any
    part_route: Any
    put_route: Any
    complete_route: Any
    submit_route: Any
    accepted: dict[str, int] = field(default_factory=dict)


def install_response_loss_flow(
    directory: Path,
    memory_keyring: MemoryKeyring,
    router: respx.MockRouter,
    *,
    loss_stage: str,
) -> ResponseLossFlow:
    """Install a one-part stateful fake that loses one accepted response."""
    if loss_stage not in {"create", "complete", "submit"}:
        raise ValueError("unsupported response-loss stage")
    idempotency_key = f"response-loss-{loss_stage}"
    manifest, attachment, local_payload = write_manifest(
        directory,
        idempotency_key=idempotency_key,
    )
    server_manifest = expected_server_manifest(local_payload, attachment)
    job_id = uuid4()
    attachment_id = uuid4()
    file_id = uuid4()
    upload_id = uuid4()
    memory_keyring.values[(SERVICE, USERNAME)] = "refresh-0"
    accepted = {"child_allocations": 0, "completions": 0, "submissions": 0}
    refresh_calls = 0
    create_calls = 0
    complete_calls = 0
    submit_calls = 0

    def refresh_response(request: httpx.Request) -> httpx.Response:
        nonlocal refresh_calls
        refresh_calls += 1
        assert json.loads(request.content) == {"refresh_token": f"refresh-{refresh_calls - 1}"}
        return httpx.Response(
            200,
            json=token_payload(
                access=f"access-{refresh_calls}",
                refresh=f"refresh-{refresh_calls}",
            ),
        )

    refresh_route = router.post(f"{ORIGIN}/api/v1/device-auth/refresh").mock(
        side_effect=refresh_response
    )

    def create_response(request: httpx.Request) -> httpx.Response:
        nonlocal create_calls
        create_calls += 1
        assert request.headers["Idempotency-Key"] == idempotency_key
        assert request.headers["Authorization"] == f"Bearer access-{refresh_calls}"
        assert json.loads(request.content) == server_manifest
        if accepted["child_allocations"] == 0:
            accepted["child_allocations"] = 1
        if loss_stage == "create" and create_calls == 1:
            raise httpx.ReadError("accepted create response was lost", request=request)
        return httpx.Response(
            201,
            json=job_payload(
                server_manifest,
                job_id=job_id,
                attachment_id=attachment_id,
                file_id=file_id,
                upload_id=upload_id,
            ),
        )

    create_route = router.post(f"{ORIGIN}/api/v1/device/import-jobs").mock(
        side_effect=create_response
    )
    part_route = router.post(
        f"{ORIGIN}/api/v1/device/import-jobs/{job_id}/attachments/{attachment_id}/parts/1"
    ).mock(
        return_value=httpx.Response(
            200,
            json={"url": "https://storage.local/response-loss-part"},
        )
    )

    def put_response(request: httpx.Request) -> httpx.Response:
        assert "Authorization" not in request.headers
        assert request.content == attachment.read_bytes()
        return httpx.Response(200, headers={"ETag": "stable-etag"})

    put_route = router.put("https://storage.local/response-loss-part").mock(
        side_effect=put_response
    )

    def complete_response(request: httpx.Request) -> httpx.Response:
        nonlocal complete_calls
        complete_calls += 1
        assert request.headers["Authorization"] == f"Bearer access-{refresh_calls}"
        assert json.loads(request.content) == {"parts": [{"part_number": 1, "etag": "stable-etag"}]}
        if accepted["completions"] == 0:
            accepted["completions"] = 1
        if loss_stage == "complete" and complete_calls == 1:
            raise httpx.ReadError("accepted complete response was lost", request=request)
        return httpx.Response(
            200,
            json=attachment_payload(
                attachment_id=attachment_id,
                file_id=file_id,
                upload_id=upload_id,
                state="QUARANTINED",
            ),
        )

    complete_route = router.post(
        f"{ORIGIN}/api/v1/device/import-jobs/{job_id}/attachments/{attachment_id}/complete"
    ).mock(side_effect=complete_response)

    def submit_response(request: httpx.Request) -> httpx.Response:
        nonlocal submit_calls
        submit_calls += 1
        assert request.headers["Authorization"] == f"Bearer access-{refresh_calls}"
        if accepted["submissions"] == 0:
            accepted["submissions"] = 1
        if loss_stage == "submit" and submit_calls == 1:
            raise httpx.ReadError("accepted submit response was lost", request=request)
        return httpx.Response(
            200,
            json={
                "id": str(job_id),
                "status": "SCANNING",
                "result_code": None,
                "submitted_at": "2026-08-10T09:55:00Z",
                "updated_at": "2026-08-10T09:55:00Z",
            },
        )

    submit_route = router.post(f"{ORIGIN}/api/v1/device/import-jobs/{job_id}/submit").mock(
        side_effect=submit_response
    )
    return ResponseLossFlow(
        manifest=manifest,
        attachment=attachment,
        idempotency_key=idempotency_key,
        job_id=job_id,
        attachment_id=attachment_id,
        refresh_route=refresh_route,
        create_route=create_route,
        part_route=part_route,
        put_route=put_route,
        complete_route=complete_route,
        submit_route=submit_route,
        accepted=accepted,
    )
