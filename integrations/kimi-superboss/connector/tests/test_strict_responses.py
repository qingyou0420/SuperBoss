from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
import respx
from conftest import (
    ORIGIN,
    SERVICE,
    USERNAME,
    MemoryKeyring,
    expected_server_manifest,
    job_payload,
    load_app,
    token_payload,
    write_manifest,
)
from typer.testing import CliRunner


@dataclass
class SinglePartFlow:
    manifest: Path
    job_id: UUID
    attachment_id: UUID
    part_route: Any
    put_route: Any
    complete_route: Any
    submit_route: Any


def _single_part_flow(
    directory: Path,
    memory_keyring: MemoryKeyring,
    router: respx.MockRouter,
    *,
    put_response: httpx.Response | None = None,
    put_callback: Any | None = None,
) -> SinglePartFlow:
    manifest, attachment, local_payload = write_manifest(directory)
    server_manifest = expected_server_manifest(local_payload, attachment)
    job_id = uuid4()
    attachment_id = uuid4()
    memory_keyring.values[(SERVICE, USERNAME)] = "refresh-secret"
    router.post(f"{ORIGIN}/api/v1/device-auth/refresh").mock(
        return_value=httpx.Response(
            200,
            json=token_payload(access="access-secret", refresh="refresh-rotated"),
        )
    )
    router.post(f"{ORIGIN}/api/v1/device/import-jobs").mock(
        return_value=httpx.Response(
            201,
            json=job_payload(
                server_manifest,
                job_id=job_id,
                attachment_id=attachment_id,
                file_id=uuid4(),
                upload_id=uuid4(),
            ),
        )
    )
    part_route = router.post(
        f"{ORIGIN}/api/v1/device/import-jobs/{job_id}/attachments/{attachment_id}/parts/1"
    ).mock(
        return_value=httpx.Response(
            200,
            json={"url": "https://storage.local/strict-put"},
        )
    )
    put_route = router.put("https://storage.local/strict-put")
    if put_callback is not None:
        put_route.mock(side_effect=put_callback)
    else:
        assert put_response is not None
        put_route.mock(return_value=put_response)
    complete_route = router.post(
        f"{ORIGIN}/api/v1/device/import-jobs/{job_id}/attachments/{attachment_id}/complete"
    ).mock(return_value=httpx.Response(500))
    submit_route = router.post(f"{ORIGIN}/api/v1/device/import-jobs/{job_id}/submit").mock(
        return_value=httpx.Response(500)
    )
    return SinglePartFlow(
        manifest=manifest,
        job_id=job_id,
        attachment_id=attachment_id,
        part_route=part_route,
        put_route=put_route,
        complete_route=complete_route,
        submit_route=submit_route,
    )


@pytest.mark.parametrize("response_fault", ["kind_mismatch", "extra_field"])
def test_create_response_is_strict_before_part_requests(
    response_fault: str,
    tmp_path: Path,
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    manifest, attachment, local_payload = write_manifest(tmp_path / "input")
    server_manifest = expected_server_manifest(local_payload, attachment)
    job_id = uuid4()
    attachment_id = uuid4()
    response = job_payload(
        server_manifest,
        job_id=job_id,
        attachment_id=attachment_id,
        file_id=uuid4(),
        upload_id=uuid4(),
    )
    if response_fault == "kind_mismatch":
        response["attachments"][0]["kind"] = "ORIGINAL"
    else:
        response["unexpected"] = "server-extra"
    memory_keyring.values[(SERVICE, USERNAME)] = "refresh-secret"
    respx_mock.post(f"{ORIGIN}/api/v1/device-auth/refresh").mock(
        return_value=httpx.Response(
            200,
            json=token_payload(access="access-secret", refresh="refresh-rotated"),
        )
    )
    respx_mock.post(f"{ORIGIN}/api/v1/device/import-jobs").mock(
        return_value=httpx.Response(201, json=response)
    )
    part = respx_mock.post(
        f"{ORIGIN}/api/v1/device/import-jobs/{job_id}/attachments/{attachment_id}/parts/1"
    ).mock(return_value=httpx.Response(500))

    result = runner.invoke(
        app,
        ["submit", "--server", ORIGIN, "--manifest", str(manifest)],
    )

    assert result.exit_code == 5
    assert not part.called
    combined = f"{result.stdout}\n{result.stderr}"
    for secret in ("access-secret", "refresh-secret", "refresh-rotated"):
        assert secret not in combined


def test_refresh_extra_json_fails_before_credential_rotation_or_business_request(
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    job_id = uuid4()
    memory_keyring.values[(SERVICE, USERNAME)] = "refresh-old"
    malformed = token_payload(access="access-new", refresh="refresh-new")
    malformed["unexpected"] = "server-extra"
    respx_mock.post(f"{ORIGIN}/api/v1/device-auth/refresh").mock(
        return_value=httpx.Response(200, json=malformed)
    )
    business = respx_mock.get(f"{ORIGIN}/api/v1/device/import-jobs/{job_id}").mock(
        return_value=httpx.Response(500)
    )

    result = runner.invoke(
        app,
        ["status", "--server", ORIGIN, "--job-id", str(job_id)],
    )

    assert result.exit_code == 3
    assert memory_keyring.values[(SERVICE, USERNAME)] == "refresh-old"
    assert not business.called
    combined = f"{result.stdout}\n{result.stderr}"
    for secret in ("refresh-old", "refresh-new", "access-new"):
        assert secret not in combined


def test_refresh_rotation_write_failure_prevents_new_access_token_use(
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    job_id = uuid4()
    memory_keyring.values[(SERVICE, USERNAME)] = "refresh-old"
    memory_keyring.write_error = RuntimeError("rotation-write-private-detail")
    respx_mock.post(f"{ORIGIN}/api/v1/device-auth/refresh").mock(
        return_value=httpx.Response(
            200,
            json=token_payload(access="access-new", refresh="refresh-new"),
        )
    )
    business = respx_mock.get(f"{ORIGIN}/api/v1/device/import-jobs/{job_id}").mock(
        return_value=httpx.Response(500)
    )

    result = runner.invoke(
        app,
        ["status", "--server", ORIGIN, "--job-id", str(job_id)],
    )

    assert result.exit_code == 3
    assert memory_keyring.values[(SERVICE, USERNAME)] == "refresh-old"
    assert not business.called
    assert not list(state_dir.rglob("*.json"))
    combined = f"{result.stdout}\n{result.stderr}"
    for secret in (
        "refresh-old",
        "refresh-new",
        "access-new",
        "rotation-write-private-detail",
    ):
        assert secret not in combined


def test_status_extra_json_is_rejected_by_strict_response_model(
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    job_id = uuid4()
    memory_keyring.values[(SERVICE, USERNAME)] = "refresh-old"
    respx_mock.post(f"{ORIGIN}/api/v1/device-auth/refresh").mock(
        return_value=httpx.Response(
            200,
            json=token_payload(access="access-new", refresh="refresh-new"),
        )
    )
    response = job_payload(
        {
            "project_id": str(uuid4()),
            "local_task_id": "strict-status",
            "external_document_reference": None,
            "base_sha256": None,
            "k3_result": {
                "model_label": "K3",
                "processed_at": "2026-08-10T09:54:00Z",
                "modification_details": [],
                "knowledge_points": [],
                "risks": [],
                "suggested_title": None,
                "suggested_tags": [],
            },
        },
        job_id=job_id,
        attachment_id=uuid4(),
        file_id=uuid4(),
        upload_id=uuid4(),
        status="SCANNING",
    )
    response["unexpected"] = "server-extra"
    respx_mock.get(f"{ORIGIN}/api/v1/device/import-jobs/{job_id}").mock(
        return_value=httpx.Response(200, json=response)
    )

    result = runner.invoke(
        app,
        ["status", "--server", ORIGIN, "--job-id", str(job_id)],
    )

    assert result.exit_code == 5
    assert "server-extra" not in f"{result.stdout}\n{result.stderr}"


def test_overlong_etag_fails_closed_before_complete(
    tmp_path: Path,
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    flow = _single_part_flow(
        tmp_path / "etag",
        memory_keyring,
        respx_mock,
        put_response=httpx.Response(200, headers={"ETag": "E" * 1025}),
    )

    result = runner.invoke(
        app,
        ["submit", "--server", ORIGIN, "--manifest", str(flow.manifest)],
    )

    assert result.exit_code == 5
    assert flow.part_route.called and flow.put_route.called
    assert not flow.complete_route.called and not flow.submit_route.called
    assert "E" * 128 not in f"{result.stdout}\n{result.stderr}"


def test_presigned_put_cross_origin_redirect_is_never_followed(
    tmp_path: Path,
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)

    def redirect(request: httpx.Request) -> httpx.Response:
        assert "Authorization" not in request.headers
        return httpx.Response(
            307,
            headers={"Location": "https://evil.example/presigned"},
        )

    flow = _single_part_flow(
        tmp_path / "redirect",
        memory_keyring,
        respx_mock,
        put_callback=redirect,
    )
    destination = respx_mock.put("https://evil.example/presigned").mock(
        return_value=httpx.Response(200, headers={"ETag": "stolen"})
    )

    result = runner.invoke(
        app,
        ["submit", "--server", ORIGIN, "--manifest", str(flow.manifest)],
    )

    assert result.exit_code == 5
    assert flow.put_route.called
    assert not destination.called
    assert not flow.complete_route.called and not flow.submit_route.called
    combined = f"{result.stdout}\n{result.stderr}"
    for secret in ("access-secret", "refresh-secret", "storage.local", "evil.example"):
        assert secret not in combined


def test_status_rejects_unsafe_result_code_before_output(
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    job_id = uuid4()
    memory_keyring.values[(SERVICE, USERNAME)] = "refresh-result-code"
    respx_mock.post(f"{ORIGIN}/api/v1/device-auth/refresh").mock(
        return_value=httpx.Response(
            200,
            json=token_payload(access="access-result-code", refresh="refresh-rotated"),
        )
    )
    response = job_payload(
        {
            "project_id": str(uuid4()),
            "local_task_id": "unsafe-result-code",
            "external_document_reference": None,
            "base_sha256": None,
            "k3_result": {
                "model_label": "K3",
                "processed_at": "2026-08-10T09:54:00Z",
                "modification_details": [],
                "knowledge_points": [],
                "risks": [],
                "suggested_title": None,
                "suggested_tags": [],
            },
        },
        job_id=job_id,
        attachment_id=uuid4(),
        file_id=uuid4(),
        upload_id=uuid4(),
        status="SCANNING",
    )
    response["result_code"] = "BAD\nINJECT"
    respx_mock.get(f"{ORIGIN}/api/v1/device/import-jobs/{job_id}").mock(
        return_value=httpx.Response(200, json=response)
    )

    result = runner.invoke(app, ["status", "--server", ORIGIN, "--job-id", str(job_id)])

    assert result.exit_code == 5
    assert "INJECT" not in f"{result.stdout}\n{result.stderr}"
