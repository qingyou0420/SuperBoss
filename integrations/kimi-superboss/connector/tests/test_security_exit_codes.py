from __future__ import annotations

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
    error_payload,
    expected_server_manifest,
    job_payload,
    load_app,
    token_payload,
    write_manifest,
)
from typer.main import get_command
from typer.testing import CliRunner


def test_cli_exposes_exact_commands_without_credential_or_tls_bypass_options(
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = load_app(monkeypatch, state_dir)
    command = get_command(app)

    assert command.commands is not None
    assert set(command.commands) == {"pair", "submit", "status", "retry"}
    forbidden = {
        "access_token",
        "refresh_token",
        "credential",
        "password",
        "insecure",
        "verify_tls",
    }
    for subcommand in command.commands.values():
        assert not ({parameter.name for parameter in subcommand.params} & forbidden)


@pytest.mark.parametrize(
    ("status_code", "expected_exit"),
    [(401, 3), (403, 3), (429, 6), (500, 6), (503, 6)],
)
def test_pair_maps_auth_and_temporary_failures_to_stable_secret_safe_exits(
    status_code: int,
    expected_exit: int,
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    respx_mock.post(f"{ORIGIN}/api/v1/device-auth/pair").mock(
        return_value=httpx.Response(status_code, json=error_payload("PAIR_REJECTED"))
    )

    result = runner.invoke(
        app,
        [
            "pair",
            "--server",
            ORIGIN,
            "--code",
            "pair-secret",
            "--name",
            "Owner-PC",
        ],
    )

    assert result.exit_code == expected_exit
    assert memory_keyring.values == {}
    assert "pair-secret" not in f"{result.stdout}\n{result.stderr}"


def test_missing_refresh_credential_exits_3_without_network(
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    monkeypatch.setenv("SUPERBOSS_ACCESS_TOKEN", "environment-access-secret")
    monkeypatch.setenv("SUPERBOSS_REFRESH_TOKEN", "environment-refresh-secret")

    result = runner.invoke(
        app,
        ["status", "--server", ORIGIN, "--job-id", str(uuid4())],
    )

    assert result.exit_code == 3
    assert memory_keyring.get_calls == [(SERVICE, USERNAME)]
    assert len(respx_mock.calls) == 0
    combined = f"{result.stdout}\n{result.stderr}"
    assert "environment-access-secret" not in combined
    assert "environment-refresh-secret" not in combined


def test_pair_credential_store_failure_exits_3_without_false_success(
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    memory_keyring.write_error = RuntimeError("credential-store-secret-detail")
    respx_mock.post(f"{ORIGIN}/api/v1/device-auth/pair").mock(
        return_value=httpx.Response(
            200,
            json=token_payload(access="access-secret", refresh="refresh-secret"),
        )
    )

    result = runner.invoke(
        app,
        ["pair", "--server", ORIGIN, "--code", "pair-secret", "--name", "Owner-PC"],
    )

    assert result.exit_code == 3
    assert memory_keyring.values == {}
    combined = f"{result.stdout}\n{result.stderr}"
    for secret in (
        "pair-secret",
        "access-secret",
        "refresh-secret",
        "credential-store-secret-detail",
    ):
        assert secret not in combined


@pytest.mark.parametrize(("status_code", "expected_exit"), [(409, 5), (422, 5), (429, 6), (503, 6)])
def test_submit_maps_stable_rejections_and_temporary_failures(
    status_code: int,
    expected_exit: int,
    tmp_path: Path,
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    manifest, _attachment, _payload = write_manifest(tmp_path / "input")
    memory_keyring.values[(SERVICE, USERNAME)] = "refresh-secret"
    respx_mock.post(f"{ORIGIN}/api/v1/device-auth/refresh").mock(
        return_value=httpx.Response(
            200,
            json=token_payload(access="access-secret", refresh="refresh-rotated"),
        )
    )
    respx_mock.post(f"{ORIGIN}/api/v1/device/import-jobs").mock(
        return_value=httpx.Response(status_code, json=error_payload("IMPORT_REJECTED"))
    )

    result = runner.invoke(
        app,
        ["submit", "--server", ORIGIN, "--manifest", str(manifest)],
    )

    assert result.exit_code == expected_exit
    combined = f"{result.stdout}\n{result.stderr}"
    for secret in ("refresh-secret", "refresh-rotated", "access-secret"):
        assert secret not in combined


def test_cross_origin_api_redirect_is_not_followed_and_bearer_never_leaks(
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    job_id = uuid4()
    memory_keyring.values[(SERVICE, USERNAME)] = "refresh-secret"
    respx_mock.post(f"{ORIGIN}/api/v1/device-auth/refresh").mock(
        return_value=httpx.Response(
            200,
            json=token_payload(access="bearer-secret", refresh="refresh-rotated"),
        )
    )
    source = respx_mock.get(f"{ORIGIN}/api/v1/device/import-jobs/{job_id}").mock(
        return_value=httpx.Response(
            307,
            headers={"Location": "https://evil.example/steal"},
        )
    )
    destination = respx_mock.get("https://evil.example/steal").mock(
        return_value=httpx.Response(200, json={})
    )

    result = runner.invoke(
        app,
        ["status", "--server", ORIGIN, "--job-id", str(job_id)],
    )

    assert result.exit_code == 5
    assert source.called
    assert not destination.called
    assert "bearer-secret" not in f"{result.stdout}\n{result.stderr}"


def test_cross_origin_refresh_redirect_is_not_followed_with_refresh_credential(
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    memory_keyring.values[(SERVICE, USERNAME)] = "refresh-secret"
    source = respx_mock.post(f"{ORIGIN}/api/v1/device-auth/refresh").mock(
        return_value=httpx.Response(
            307,
            headers={"Location": "https://evil.example/refresh"},
        )
    )
    destination = respx_mock.post("https://evil.example/refresh").mock(
        return_value=httpx.Response(200, json={})
    )

    result = runner.invoke(
        app,
        ["status", "--server", ORIGIN, "--job-id", str(uuid4())],
    )

    assert result.exit_code == 3
    assert source.called
    assert not destination.called
    assert "refresh-secret" not in f"{result.stdout}\n{result.stderr}"


def test_missing_etag_fails_closed_before_completion_or_submit(
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
    memory_keyring.values[(SERVICE, USERNAME)] = "refresh-secret"
    respx_mock.post(f"{ORIGIN}/api/v1/device-auth/refresh").mock(
        return_value=httpx.Response(
            200,
            json=token_payload(access="access-secret", refresh="refresh-rotated"),
        )
    )
    respx_mock.post(f"{ORIGIN}/api/v1/device/import-jobs").mock(
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
    respx_mock.post(
        f"{ORIGIN}/api/v1/device/import-jobs/{job_id}/attachments/{attachment_id}/parts/1"
    ).mock(
        return_value=httpx.Response(
            200,
            json={"url": "https://storage.local/missing-etag"},
        )
    )
    respx_mock.put("https://storage.local/missing-etag").mock(return_value=httpx.Response(200))
    complete = respx_mock.post(
        f"{ORIGIN}/api/v1/device/import-jobs/{job_id}/attachments/{attachment_id}/complete"
    ).mock(return_value=httpx.Response(500))
    submit = respx_mock.post(f"{ORIGIN}/api/v1/device/import-jobs/{job_id}/submit").mock(
        return_value=httpx.Response(500)
    )

    result = runner.invoke(
        app,
        ["submit", "--server", ORIGIN, "--manifest", str(manifest)],
    )

    assert result.exit_code == 5
    assert not complete.called and not submit.called
    combined = f"{result.stdout}\n{result.stderr}"
    for secret in ("access-secret", "refresh-secret", "storage.local"):
        assert secret not in combined


def test_corrupt_outbox_exits_2_before_credentials_or_network(
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    state_dir.mkdir(parents=True)
    (state_dir / "truncated.json").write_text('{"job_id":', encoding="utf-8")

    result = runner.invoke(app, ["retry", "--server", ORIGIN])

    assert result.exit_code == 2
    assert memory_keyring.get_calls == []
    assert len(respx_mock.calls) == 0
