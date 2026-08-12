from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import respx
from conftest import (
    ORIGIN,
    SERVICE,
    USERNAME,
    MemoryKeyring,
    job_payload,
    load_app,
    token_payload,
)
from typer.testing import CliRunner


def test_pair_stores_only_refresh_under_normalized_origin(
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    route = respx_mock.post(f"{ORIGIN}/api/v1/device-auth/pair").mock(
        return_value=httpx.Response(
            200,
            json=token_payload(access="access-secret", refresh="refresh-secret"),
        )
    )

    result = runner.invoke(
        app,
        [
            "pair",
            "--server",
            "HTTPS://NightForest.COM:443/",
            "--code",
            "pair-code-secret",
            "--name",
            "Owner-PC",
        ],
    )

    assert result.exit_code == 0
    assert route.call_count == 1
    assert json.loads(route.calls[0].request.content) == {
        "pairing_code": "pair-code-secret",
        "device_name": "Owner-PC",
    }
    assert memory_keyring.values == {(SERVICE, USERNAME): "refresh-secret"}
    combined = f"{result.stdout}\n{result.stderr}"
    for secret in ("pair-code-secret", "access-secret", "refresh-secret"):
        assert secret not in combined


def test_pair_accepts_the_backend_canonical_bearer_token_type(
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    route = respx_mock.post(f"{ORIGIN}/api/v1/device-auth/pair").mock(
        return_value=httpx.Response(
            200,
            json={
                **token_payload(access="canonical-access", refresh="canonical-refresh"),
                "token_type": "Bearer",
            },
        )
    )

    result = runner.invoke(
        app,
        [
            "pair",
            "--server",
            ORIGIN,
            "--code",
            "canonical-pair-code",
            "--name",
            "Owner-PC",
        ],
    )

    assert result.exit_code == 0
    assert route.call_count == 1
    assert memory_keyring.values == {(SERVICE, USERNAME): "canonical-refresh"}
    combined = f"{result.stdout}\n{result.stderr}"
    for secret in ("canonical-pair-code", "canonical-access", "canonical-refresh"):
        assert secret not in combined


def test_pair_rejects_a_noncanonical_token_type_before_credential_save(
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    response = token_payload(access="lowercase-access", refresh="lowercase-refresh")
    response["token_type"] = "bearer"
    route = respx_mock.post(f"{ORIGIN}/api/v1/device-auth/pair").mock(
        return_value=httpx.Response(200, json=response)
    )

    result = runner.invoke(
        app,
        [
            "pair",
            "--server",
            ORIGIN,
            "--code",
            "lowercase-pair-code",
            "--name",
            "Owner-PC",
        ],
    )

    assert result.exit_code == 3
    assert route.call_count == 1
    assert memory_keyring.values == {}
    combined = f"{result.stdout}\n{result.stderr}"
    for secret in ("lowercase-pair-code", "lowercase-access", "lowercase-refresh"):
        assert secret not in combined


@pytest.mark.parametrize(
    "origin",
    [
        "http://nightforest.com",
        "https://user@nightforest.com",
        "https://nightforest.com/api",
        "https://nightforest.com?debug=1",
        "https://nightforest.com#fragment",
        "ftp://nightforest.com",
    ],
)
def test_invalid_origins_exit_2_before_keyring_or_network(
    origin: str,
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)

    result = runner.invoke(
        app,
        ["pair", "--server", origin, "--code", "short-lived", "--name", "Owner-PC"],
    )

    assert result.exit_code == 2
    assert memory_keyring.get_calls == []
    assert memory_keyring.set_calls == []
    assert len(respx_mock.calls) == 0
    assert "short-lived" not in f"{result.stdout}\n{result.stderr}"


@pytest.mark.parametrize(
    ("input_origin", "normalized"),
    [
        ("http://localhost:8123/", "http://localhost:8123"),
        ("http://127.0.0.1:8123", "http://127.0.0.1:8123"),
        ("http://[::1]:8123/", "http://[::1]:8123"),
    ],
)
def test_explicit_loopback_http_is_permitted(
    input_origin: str,
    normalized: str,
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    route = respx_mock.post(f"{normalized}/api/v1/device-auth/pair").mock(
        return_value=httpx.Response(
            200,
            json=token_payload(access="access-local", refresh="refresh-local"),
        )
    )

    result = runner.invoke(
        app,
        ["pair", "--server", input_origin, "--code", "local-code", "--name", "Dev-PC"],
    )

    assert result.exit_code == 0
    assert route.called
    assert memory_keyring.values == {
        (f"SuperBoss/KimiConnector/{normalized}", USERNAME): "refresh-local"
    }


def test_refresh_rotates_credential_before_bearer_use_and_status_is_bounded(
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    job_id = uuid4()
    memory_keyring.values[(SERVICE, USERNAME)] = "refresh-old"
    refreshed = respx_mock.post(f"{ORIGIN}/api/v1/device-auth/refresh").mock(
        return_value=httpx.Response(
            200,
            json=token_payload(access="access-new", refresh="refresh-new"),
        )
    )

    def status_response(request: httpx.Request) -> httpx.Response:
        assert memory_keyring.values[(SERVICE, USERNAME)] == "refresh-new"
        assert request.headers["Authorization"] == "Bearer access-new"
        body = job_payload(
            {
                "project_id": str(uuid4()),
                "local_task_id": "local-status",
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
        return httpx.Response(200, json=body)

    status = respx_mock.get(f"{ORIGIN}/api/v1/device/import-jobs/{job_id}").mock(
        side_effect=status_response
    )

    result = runner.invoke(
        app,
        ["status", "--server", ORIGIN, "--job-id", str(job_id)],
    )

    assert result.exit_code == 0
    assert refreshed.called and status.called
    assert str(job_id) in result.stdout
    assert "SCANNING" in result.stdout
    assert all(
        word not in result.stdout.lower() for word in ("archived", "complete", "归档", "完成")
    )
    combined = f"{result.stdout}\n{result.stderr}"
    for secret in ("refresh-old", "refresh-new", "access-new"):
        assert secret not in combined


def test_unpaired_unicode_surrogate_exits_2_before_keyring_or_network(
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)

    result = runner.invoke(
        app,
        ["pair", "--server", ORIGIN, "--code", "local-code", "--name", "bad\ud800name"],
    )

    assert result.exit_code == 2
    assert memory_keyring.get_calls == []
    assert memory_keyring.set_calls == []
    assert len(respx_mock.calls) == 0
