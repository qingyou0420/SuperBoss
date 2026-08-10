from __future__ import annotations

import hashlib
import importlib
import json
import os
import threading
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
import respx
import typer
from conftest import (
    ORIGIN,
    SERVICE,
    USERNAME,
    MemoryKeyring,
    install_response_loss_flow,
    job_payload,
    load_app,
    token_payload,
    write_manifest,
)
from typer.testing import CliRunner

from superboss_connector.errors import OUTBOX_INVALID, ConnectorError


def _assert_state_has_no_secrets(state_dir: Path) -> None:
    secrets = (b"refresh-1", b"new-refresh", b"new-access", b"one-time-pair")
    for path in state_dir.rglob("*"):
        if path.is_file():
            content = path.read_bytes()
            for secret in secrets:
                assert secret not in content


def _marker_document(
    state_dir: Path,
    *,
    old_credential_state: str = "PRESENT",
) -> tuple[Path, dict[str, Any]]:
    markers = list(state_dir.rglob("replacement.marker"))
    assert len(markers) == 1
    document = json.loads(markers[0].read_text(encoding="utf-8"))
    serialized = markers[0].read_text(encoding="utf-8").lower()
    for secret in ("refresh-1", "new-refresh", "new-access", "one-time-pair"):
        assert secret not in serialized
    assert document["old_credential_state"] == old_credential_state
    expected_fingerprint = (
        hashlib.sha256(b"refresh-1").hexdigest() if old_credential_state == "PRESENT" else None
    )
    assert document["old_refresh_sha256"] == expected_fingerprint
    _assert_state_has_no_secrets(state_dir)
    return markers[0], document


def _initial_pair_marker_document(
    state_dir: Path,
    *,
    old_refresh: str | None = None,
) -> dict[str, Any]:
    markers = list(state_dir.rglob("pair-completion.marker"))
    assert len(markers) == 1
    document = json.loads(markers[0].read_text(encoding="utf-8"))
    serialized = markers[0].read_text(encoding="utf-8").lower()
    for secret in ("initial-code", "initial-access", "initial-refresh"):
        assert secret not in serialized
    assert document["normalized_origin"] == ORIGIN
    expected_state = "MISSING" if old_refresh is None else "PRESENT"
    expected_fingerprint = (
        None if old_refresh is None else hashlib.sha256(old_refresh.encode("utf-8")).hexdigest()
    )
    assert document["old_credential_state"] == expected_state
    assert document["old_refresh_sha256"] == expected_fingerprint
    return document


def _install_old_submit(
    *,
    tmp_path: Path,
    app: Any,
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    respx_mock: respx.MockRouter,
) -> tuple[Any, Path]:
    flow = install_response_loss_flow(
        tmp_path,
        memory_keyring,
        respx_mock,
        loss_stage="submit",
    )
    first = runner.invoke(
        app,
        ["submit", "--server", ORIGIN, "--manifest", str(flow.manifest)],
    )
    assert first.exit_code == 6
    entry = next(state_dir.rglob("*.json"))
    assert json.loads(entry.read_text(encoding="utf-8"))["phase"] == "SUBMIT"
    assert memory_keyring.values[(SERVICE, USERNAME)] == "refresh-1"
    return flow, entry


def _pair_route(router: respx.MockRouter, *, status_code: int = 200) -> Any:
    if status_code != 200:
        return router.post(f"{ORIGIN}/api/v1/device-auth/pair").mock(
            return_value=httpx.Response(status_code)
        )
    return router.post(f"{ORIGIN}/api/v1/device-auth/pair").mock(
        return_value=httpx.Response(
            200,
            json=token_payload(access="new-access", refresh="new-refresh"),
        )
    )


def _run_pair(app: Any, runner: CliRunner) -> Any:
    return runner.invoke(
        app,
        [
            "pair",
            "--server",
            ORIGIN,
            "--code",
            "one-time-pair",
            "--name",
            "Replacement-PC",
        ],
    )


def _configure_new_submit(
    flow: Any,
    *,
    tmp_path: Path,
    memory_keyring: MemoryKeyring,
) -> Path:
    assert memory_keyring.values[(SERVICE, USERNAME)] == "new-refresh"
    flow.refresh_route.mock(
        return_value=httpx.Response(
            200,
            json=token_payload(access="new-submit-access", refresh="new-submit-refresh"),
        )
    )
    flow.create_route.mock(return_value=httpx.Response(503))
    manifest, _attachment, _payload = write_manifest(
        tmp_path,
        idempotency_key=f"kimi-{uuid4()}",
    )
    return manifest


@pytest.mark.parametrize("old_refresh", [None, "existing-initial-refresh"])
@pytest.mark.parametrize("output_error", [BrokenPipeError, OSError])
def test_initial_pair_output_failure_is_durable_and_replays_without_remote_pair(
    output_error: type[OSError],
    old_refresh: str | None,
    runner: CliRunner,
    capsys: pytest.CaptureFixture[str],
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    cli_module = importlib.import_module("superboss_connector.cli")
    if old_refresh is not None:
        memory_keyring.values[(SERVICE, USERNAME)] = old_refresh
    pair_route = respx_mock.post(f"{ORIGIN}/api/v1/device-auth/pair").mock(
        return_value=httpx.Response(
            200,
            json=token_payload(access="initial-access", refresh="initial-refresh"),
        )
    )
    real_echo = cli_module.typer.echo
    failed_once = False

    def fail_success_once(message: object = None, **kwargs: object) -> None:
        nonlocal failed_once
        if not failed_once and str(message) == "Device paired.":
            failed_once = True
            raise output_error("initial-output-private-detail")
        real_echo(message, **kwargs)

    monkeypatch.setattr(cli_module.typer, "echo", fail_success_once)
    arguments = [
        "pair",
        "--server",
        ORIGIN,
        "--code",
        "initial-code",
        "--name",
        "Initial-PC",
    ]

    with pytest.raises(typer.Exit) as interrupted:
        cli_module.pair(
            server=ORIGIN,
            code="initial-code",
            name="Initial-PC",
        )

    assert interrupted.value.exit_code == 2
    assert failed_once
    assert pair_route.call_count == 1
    assert memory_keyring.values[(SERVICE, USERNAME)] == "initial-refresh"
    _initial_pair_marker_document(state_dir, old_refresh=old_refresh)
    captured_output = capsys.readouterr()
    combined = f"{captured_output.out}\n{captured_output.err}"
    for secret in (
        "initial-output-private-detail",
        "initial-code",
        "initial-access",
        "initial-refresh",
        "Traceback",
        "Another operation is active",
    ):
        assert secret not in combined
    assert "pair-completion.marker" not in combined
    assert "old_refresh_sha256" not in combined
    if old_refresh is not None:
        assert hashlib.sha256(old_refresh.encode("utf-8")).hexdigest() not in combined

    recovered = runner.invoke(app, arguments)

    assert recovered.exit_code == 0
    assert pair_route.call_count == 1
    assert "Device paired." in recovered.stdout
    assert not list(state_dir.rglob("pair-completion.marker"))


@pytest.mark.parametrize("old_refresh", [None, "old-device-refresh"])
@pytest.mark.parametrize("blocked_command", ["status", "submit"])
def test_failed_initial_pair_blocks_credential_mutation_until_real_second_pair(
    old_refresh: str | None,
    blocked_command: str,
    tmp_path: Path,
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    if old_refresh is not None:
        memory_keyring.values[(SERVICE, USERNAME)] = old_refresh
    pair_responses = iter(
        [
            httpx.Response(503),
            httpx.Response(
                200,
                json=token_payload(access="repaired-access", refresh="repaired-refresh"),
            ),
        ]
    )
    pair_route = respx_mock.post(f"{ORIGIN}/api/v1/device-auth/pair").mock(
        side_effect=lambda _request: next(pair_responses)
    )
    refresh_route = respx_mock.post(f"{ORIGIN}/api/v1/device-auth/refresh").mock(
        return_value=httpx.Response(
            200,
            json=token_payload(access="old-device-access", refresh="old-device-rotated"),
        )
    )
    job_id = uuid4()
    business_route = respx_mock.get(f"{ORIGIN}/api/v1/device/import-jobs/{job_id}").mock(
        return_value=httpx.Response(500)
    )

    first_pair = _run_pair(app, runner)

    assert first_pair.exit_code == 6
    assert pair_route.call_count == 1
    _initial_pair_marker_document(state_dir, old_refresh=old_refresh)

    if blocked_command == "status":
        blocked = runner.invoke(
            app,
            ["status", "--server", ORIGIN, "--job-id", str(job_id)],
        )
    else:
        manifest, _attachment, _payload = write_manifest(
            tmp_path / "blocked-submit",
            idempotency_key=f"kimi-{uuid4()}",
        )
        business_route = respx_mock.post(f"{ORIGIN}/api/v1/device/import-jobs").mock(
            return_value=httpx.Response(500)
        )
        blocked = runner.invoke(
            app,
            ["submit", "--server", ORIGIN, "--manifest", str(manifest)],
        )

    assert blocked.exit_code == 2
    assert refresh_route.call_count == 0
    assert business_route.call_count == 0
    assert pair_route.call_count == 1
    assert memory_keyring.values.get((SERVICE, USERNAME)) == old_refresh
    _initial_pair_marker_document(state_dir, old_refresh=old_refresh)
    blocked_output = f"{blocked.stdout}\n{blocked.stderr}"
    for secret in (
        "old-device-refresh",
        "old-device-access",
        "old-device-rotated",
        "repaired-access",
        "repaired-refresh",
    ):
        assert secret not in blocked_output
    assert "pair-completion.marker" not in blocked_output
    assert "old_refresh_sha256" not in blocked_output
    if old_refresh is not None:
        assert hashlib.sha256(old_refresh.encode("utf-8")).hexdigest() not in blocked_output

    repaired = _run_pair(app, runner)

    assert repaired.exit_code == 0
    assert pair_route.call_count == 2
    assert memory_keyring.values[(SERVICE, USERNAME)] == "repaired-refresh"
    assert not list(state_dir.rglob("pair-completion.marker"))


def test_initial_pair_marker_save_failure_prevents_remote_pair(
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    outbox_module = importlib.import_module("superboss_connector.outbox")
    memory_keyring.values[(SERVICE, USERNAME)] = "marker-save-old-refresh"
    pair_route = _pair_route(respx_mock)

    def fail_marker_save(*_args: object, **_kwargs: object) -> None:
        raise ConnectorError(2, OUTBOX_INVALID)

    monkeypatch.setattr(
        outbox_module.OutboxStore,
        "save_pair_completion_marker",
        fail_marker_save,
    )

    result = _run_pair(app, runner)

    assert result.exit_code == 2
    assert pair_route.call_count == 0
    assert memory_keyring.values[(SERVICE, USERNAME)] == "marker-save-old-refresh"
    assert not list(state_dir.rglob("pair-completion.marker"))
    combined = f"{result.stdout}\n{result.stderr}"
    assert "marker-save-old-refresh" not in combined
    assert hashlib.sha256(b"marker-save-old-refresh").hexdigest() not in combined
    assert "pair-completion.marker" not in combined


@pytest.mark.parametrize("old_refresh", [None, "keyring-save-old-refresh"])
def test_initial_pair_keyring_save_failure_requires_real_second_pair(
    old_refresh: str | None,
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    if old_refresh is not None:
        memory_keyring.values[(SERVICE, USERNAME)] = old_refresh
    pair_route = _pair_route(respx_mock)
    memory_keyring.write_error = OSError("keyring-save-private-detail")

    failed = _run_pair(app, runner)

    assert failed.exit_code == 3
    assert pair_route.call_count == 1
    assert memory_keyring.values.get((SERVICE, USERNAME)) == old_refresh
    _initial_pair_marker_document(state_dir, old_refresh=old_refresh)
    failed_output = f"{failed.stdout}\n{failed.stderr}"
    assert "keyring-save-private-detail" not in failed_output
    assert "pair-completion.marker" not in failed_output
    if old_refresh is not None:
        assert hashlib.sha256(old_refresh.encode("utf-8")).hexdigest() not in failed_output

    memory_keyring.write_error = None
    repaired = _run_pair(app, runner)

    assert repaired.exit_code == 0
    assert pair_route.call_count == 2
    assert memory_keyring.values[(SERVICE, USERNAME)] == "new-refresh"
    assert not list(state_dir.rglob("pair-completion.marker"))


@pytest.mark.parametrize("old_refresh", [None, "marker-delete-old-refresh"])
def test_initial_pair_marker_delete_failure_replays_without_second_remote_pair(
    old_refresh: str | None,
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    outbox_module = importlib.import_module("superboss_connector.outbox")
    if old_refresh is not None:
        memory_keyring.values[(SERVICE, USERNAME)] = old_refresh
    pair_route = _pair_route(respx_mock)
    real_delete = outbox_module.OutboxStore.delete_pair_completion_marker
    failed_once = False

    def fail_delete_once(store: Any) -> None:
        nonlocal failed_once
        if not failed_once:
            failed_once = True
            raise ConnectorError(2, OUTBOX_INVALID)
        real_delete(store)

    monkeypatch.setattr(
        outbox_module.OutboxStore,
        "delete_pair_completion_marker",
        fail_delete_once,
    )

    failed = _run_pair(app, runner)

    assert failed.exit_code == 2
    assert pair_route.call_count == 1
    assert memory_keyring.values[(SERVICE, USERNAME)] == "new-refresh"
    _initial_pair_marker_document(state_dir, old_refresh=old_refresh)
    failed_output = f"{failed.stdout}\n{failed.stderr}"
    assert "pair-completion.marker" not in failed_output
    if old_refresh is not None:
        assert hashlib.sha256(old_refresh.encode("utf-8")).hexdigest() not in failed_output

    recovered = _run_pair(app, runner)

    assert recovered.exit_code == 0
    assert pair_route.call_count == 1
    assert "Device paired." in recovered.stdout
    assert not list(state_dir.rglob("pair-completion.marker"))


@pytest.mark.parametrize("output_error", [BrokenPipeError, OSError])
def test_status_output_failure_is_stable_2_and_secret_safe(
    output_error: type[OSError],
    capsys: pytest.CaptureFixture[str],
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    load_app(monkeypatch, state_dir)
    cli_module = importlib.import_module("superboss_connector.cli")
    job_id = uuid4()
    memory_keyring.values[(SERVICE, USERNAME)] = "status-output-refresh"
    respx_mock.post(f"{ORIGIN}/api/v1/device-auth/refresh").mock(
        return_value=httpx.Response(
            200,
            json=token_payload(
                access="status-output-access",
                refresh="status-output-rotated",
            ),
        )
    )
    response = job_payload(
        {
            "project_id": str(uuid4()),
            "local_task_id": "status-output",
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
    status_route = respx_mock.get(f"{ORIGIN}/api/v1/device/import-jobs/{job_id}").mock(
        return_value=httpx.Response(200, json=response)
    )
    real_echo = cli_module.typer.echo
    failed_once = False

    def fail_status_once(message: object = None, **kwargs: object) -> None:
        nonlocal failed_once
        if not failed_once and str(message).startswith(str(job_id)):
            failed_once = True
            raise output_error("status-output-private-detail")
        real_echo(message, **kwargs)

    monkeypatch.setattr(cli_module.typer, "echo", fail_status_once)

    with pytest.raises(typer.Exit) as result:
        cli_module.status(server=ORIGIN, job_id=job_id)

    assert result.value.exit_code == 2
    assert failed_once
    assert status_route.call_count == 1
    assert memory_keyring.values[(SERVICE, USERNAME)] == "status-output-rotated"
    assert not list(state_dir.rglob("*.marker"))
    assert not list(state_dir.rglob("*.json"))
    captured_output = capsys.readouterr()
    combined = f"{captured_output.out}\n{captured_output.err}"
    for secret in (
        "status-output-private-detail",
        "status-output-access",
        "status-output-refresh",
        "status-output-rotated",
        "Traceback",
        "Another operation is active",
    ):
        assert secret not in combined


def test_successful_repair_abandons_old_job_and_new_key_can_reach_create(
    tmp_path: Path,
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    flow, old_entry = _install_old_submit(
        tmp_path=tmp_path / "old",
        app=app,
        runner=runner,
        memory_keyring=memory_keyring,
        state_dir=state_dir,
        respx_mock=respx_mock,
    )
    pair_route = _pair_route(respx_mock)

    paired = _run_pair(app, runner)

    assert paired.exit_code == 0
    assert pair_route.call_count == 1
    assert not old_entry.exists()
    assert not list(state_dir.rglob("replacement.marker"))
    lower_output = f"{paired.stdout}\n{paired.stderr}".lower()
    assert any(term in lower_output for term in ("abandon", "discard"))
    assert "new" in lower_output and "manifest" in lower_output and "uuid" in lower_output
    new_manifest = _configure_new_submit(
        flow,
        tmp_path=tmp_path / "new",
        memory_keyring=memory_keyring,
    )

    submitted = runner.invoke(
        app,
        ["submit", "--server", ORIGIN, "--manifest", str(new_manifest)],
    )

    assert submitted.exit_code == 6
    assert flow.create_route.call_count == 2
    assert flow.submit_route.call_count == 1
    combined = f"{paired.stdout}\n{paired.stderr}\n{submitted.stdout}\n{submitted.stderr}"
    for secret in ("refresh-1", "new-refresh", "new-access", "one-time-pair"):
        assert secret not in combined


@pytest.mark.parametrize(
    ("loss_stage", "expected_phase"),
    [("complete", "UPLOAD"), ("submit", "SUBMIT")],
)
def test_missing_old_refresh_can_repair_pending_state_and_start_new_submission(
    loss_stage: str,
    expected_phase: str,
    tmp_path: Path,
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    flow = install_response_loss_flow(
        tmp_path / "old",
        memory_keyring,
        respx_mock,
        loss_stage=loss_stage,
    )
    first = runner.invoke(
        app,
        ["submit", "--server", ORIGIN, "--manifest", str(flow.manifest)],
    )
    assert first.exit_code == 6
    old_entry = next(state_dir.rglob("*.json"))
    assert json.loads(old_entry.read_text(encoding="utf-8"))["phase"] == expected_phase
    old_complete_calls = flow.complete_route.call_count
    old_submit_calls = flow.submit_route.call_count
    memory_keyring.values.pop((SERVICE, USERNAME))
    pair_route = _pair_route(respx_mock)

    paired = _run_pair(app, runner)

    assert paired.exit_code == 0
    assert pair_route.call_count == 1
    assert memory_keyring.values[(SERVICE, USERNAME)] == "new-refresh"
    assert not old_entry.exists()
    assert not list(state_dir.rglob("replacement.marker"))
    assert any(
        word in f"{paired.stdout}\n{paired.stderr}".lower() for word in ("abandon", "discard")
    )
    new_manifest = _configure_new_submit(
        flow,
        tmp_path=tmp_path / "new",
        memory_keyring=memory_keyring,
    )

    submitted = runner.invoke(
        app,
        ["submit", "--server", ORIGIN, "--manifest", str(new_manifest)],
    )

    assert submitted.exit_code == 6
    assert flow.create_route.call_count == 2
    assert flow.complete_route.call_count == old_complete_calls
    assert flow.submit_route.call_count == old_submit_calls
    combined = f"{paired.stdout}\n{paired.stderr}\n{submitted.stdout}\n{submitted.stderr}"
    for secret in ("refresh-1", "new-refresh", "new-access", "one-time-pair"):
        assert secret not in combined


def test_missing_old_refresh_keyring_save_failure_can_pair_again_without_deadlock(
    tmp_path: Path,
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    flow, old_entry = _install_old_submit(
        tmp_path=tmp_path,
        app=app,
        runner=runner,
        memory_keyring=memory_keyring,
        state_dir=state_dir,
        respx_mock=respx_mock,
    )
    memory_keyring.values.pop((SERVICE, USERNAME))
    pair_route = _pair_route(respx_mock)
    memory_keyring.write_error = RuntimeError("keyring-write-private-detail")

    failed = _run_pair(app, runner)

    assert failed.exit_code == 3
    assert pair_route.call_count == 1
    assert old_entry.exists()
    _marker_document(state_dir, old_credential_state="MISSING")
    assert (SERVICE, USERNAME) not in memory_keyring.values
    combined = f"{failed.stdout}\n{failed.stderr}"
    for secret in ("keyring-write-private-detail", "new-refresh", "new-access"):
        assert secret not in combined
    memory_keyring.write_error = None

    repaired = _run_pair(app, runner)

    assert repaired.exit_code == 0
    assert pair_route.call_count == 2
    assert memory_keyring.values[(SERVICE, USERNAME)] == "new-refresh"
    assert not old_entry.exists()
    assert not list(state_dir.rglob("replacement.marker"))
    assert flow.submit_route.call_count == 1


def test_missing_old_refresh_new_credential_crash_recovery_abandons_old_state(
    tmp_path: Path,
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    flow, old_entry = _install_old_submit(
        tmp_path=tmp_path / "old",
        app=app,
        runner=runner,
        memory_keyring=memory_keyring,
        state_dir=state_dir,
        respx_mock=respx_mock,
    )
    memory_keyring.values.pop((SERVICE, USERNAME))
    _pair_route(respx_mock)
    real_unlink = Path.unlink

    def fail_old_unlink(path: Path, missing_ok: bool = False) -> None:
        if path.resolve() == old_entry.resolve():
            raise OSError("old-outbox-delete-private-detail")
        real_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fail_old_unlink)

    failed = _run_pair(app, runner)

    assert failed.exit_code == 2
    assert memory_keyring.values[(SERVICE, USERNAME)] == "new-refresh"
    assert old_entry.exists()
    _marker_document(state_dir, old_credential_state="MISSING")
    assert flow.submit_route.call_count == 1
    monkeypatch.setattr(Path, "unlink", real_unlink)
    new_manifest = _configure_new_submit(
        flow,
        tmp_path=tmp_path / "new",
        memory_keyring=memory_keyring,
    )

    submitted = runner.invoke(
        app,
        ["submit", "--server", ORIGIN, "--manifest", str(new_manifest)],
    )

    assert submitted.exit_code == 6
    assert flow.submit_route.call_count == 1
    assert flow.create_route.call_count == 2
    assert not old_entry.exists()
    assert not list(state_dir.rglob("replacement.marker"))


@pytest.mark.parametrize(
    "old_credential_state",
    ["PRESENT", "MISSING"],
)
def test_rerun_pair_finishes_durable_replacement_without_second_remote_pair(
    old_credential_state: str,
    tmp_path: Path,
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    flow, old_entry = _install_old_submit(
        tmp_path=tmp_path,
        app=app,
        runner=runner,
        memory_keyring=memory_keyring,
        state_dir=state_dir,
        respx_mock=respx_mock,
    )
    if old_credential_state == "MISSING":
        memory_keyring.values.pop((SERVICE, USERNAME))
    pair_route = _pair_route(respx_mock)
    real_unlink = Path.unlink

    def fail_old_unlink(path: Path, missing_ok: bool = False) -> None:
        if path.resolve() == old_entry.resolve():
            raise OSError("old-outbox-delete-private-detail")
        real_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fail_old_unlink)
    interrupted = _run_pair(app, runner)
    assert interrupted.exit_code == 2
    assert pair_route.call_count == 1
    assert memory_keyring.values[(SERVICE, USERNAME)] == "new-refresh"
    assert old_entry.exists()
    _marker_document(state_dir, old_credential_state=old_credential_state)
    monkeypatch.setattr(Path, "unlink", real_unlink)

    recovered = _run_pair(app, runner)

    assert recovered.exit_code == 0
    assert pair_route.call_count == 1
    assert flow.submit_route.call_count == 1
    assert not old_entry.exists()
    assert not list(state_dir.rglob("replacement.marker"))
    lower_output = f"{recovered.stdout}\n{recovered.stderr}".lower()
    assert any(word in lower_output for word in ("abandon", "discard"))
    assert "new" in lower_output and "manifest" in lower_output and "uuid" in lower_output


def test_replacement_success_output_failure_keeps_marker_for_zero_post_replay(
    tmp_path: Path,
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    cli_module = importlib.import_module("superboss_connector.cli")
    flow, old_entry = _install_old_submit(
        tmp_path=tmp_path,
        app=app,
        runner=runner,
        memory_keyring=memory_keyring,
        state_dir=state_dir,
        respx_mock=respx_mock,
    )
    pair_route = _pair_route(respx_mock)
    real_echo = cli_module.typer.echo
    failed_once = False

    def fail_replacement_output_once(message: object = None, **kwargs: object) -> None:
        nonlocal failed_once
        if not failed_once and "Old operation abandoned" in str(message):
            failed_once = True
            raise OSError("output-private-detail")
        real_echo(message, **kwargs)

    monkeypatch.setattr(cli_module.typer, "echo", fail_replacement_output_once)

    interrupted = _run_pair(app, runner)

    assert interrupted.exit_code == 2
    assert failed_once
    assert pair_route.call_count == 1
    assert memory_keyring.values[(SERVICE, USERNAME)] == "new-refresh"
    assert not old_entry.exists()
    _marker_document(state_dir)
    assert "output-private-detail" not in f"{interrupted.stdout}\n{interrupted.stderr}"
    assert flow.submit_route.call_count == 1

    recovered = _run_pair(app, runner)

    assert recovered.exit_code == 0
    assert pair_route.call_count == 1
    assert not list(state_dir.rglob("replacement.marker"))
    lower_output = f"{recovered.stdout}\n{recovered.stderr}".lower()
    assert any(word in lower_output for word in ("abandon", "discard"))
    assert "new" in lower_output and "manifest" in lower_output and "uuid" in lower_output


def test_present_old_refresh_missing_during_marker_recovery_preserves_old_state(
    tmp_path: Path,
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    flow, old_entry = _install_old_submit(
        tmp_path=tmp_path,
        app=app,
        runner=runner,
        memory_keyring=memory_keyring,
        state_dir=state_dir,
        respx_mock=respx_mock,
    )
    _pair_route(respx_mock, status_code=503)
    failed_pair = _run_pair(app, runner)
    assert failed_pair.exit_code == 6
    _marker_document(state_dir)
    memory_keyring.values.pop((SERVICE, USERNAME))

    recovered = runner.invoke(app, ["retry", "--server", ORIGIN])

    assert recovered.exit_code == 3
    assert old_entry.exists()
    assert not list(state_dir.rglob("replacement.marker"))
    assert flow.submit_route.call_count == 1
    assert "refresh-1" not in f"{recovered.stdout}\n{recovered.stderr}"


def test_repair_marker_write_failure_happens_before_remote_pair(
    tmp_path: Path,
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    flow, old_entry = _install_old_submit(
        tmp_path=tmp_path,
        app=app,
        runner=runner,
        memory_keyring=memory_keyring,
        state_dir=state_dir,
        respx_mock=respx_mock,
    )
    route = _pair_route(respx_mock)
    outbox_module = importlib.import_module("superboss_connector.outbox")
    real_replace = os.replace

    def fail_marker_replace(source: object, destination: object) -> None:
        if Path(destination).name == "replacement.marker":
            raise OSError("marker-write-private-detail")
        real_replace(source, destination)

    monkeypatch.setattr(outbox_module.os, "replace", fail_marker_replace)

    paired = _run_pair(app, runner)

    assert paired.exit_code == 2
    assert route.call_count == 0
    assert old_entry.exists()
    assert memory_keyring.values[(SERVICE, USERNAME)] == "refresh-1"
    assert "marker-write-private-detail" not in f"{paired.stdout}\n{paired.stderr}"
    _assert_state_has_no_secrets(state_dir)
    monkeypatch.setattr(outbox_module.os, "replace", real_replace)

    recovered = runner.invoke(app, ["retry", "--server", ORIGIN])

    assert recovered.exit_code == 0
    assert flow.submit_route.call_count == 2
    assert not old_entry.exists()


def test_remote_pair_failure_marker_recovers_old_identity_and_outbox(
    tmp_path: Path,
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    flow, old_entry = _install_old_submit(
        tmp_path=tmp_path,
        app=app,
        runner=runner,
        memory_keyring=memory_keyring,
        state_dir=state_dir,
        respx_mock=respx_mock,
    )
    route = _pair_route(respx_mock, status_code=503)

    paired = _run_pair(app, runner)

    assert paired.exit_code == 6
    assert route.call_count == 1
    assert old_entry.exists()
    _marker_document(state_dir)

    recovered = runner.invoke(app, ["retry", "--server", ORIGIN])

    assert recovered.exit_code == 0
    assert str(flow.job_id) in recovered.stdout
    assert flow.submit_route.call_count == 2
    assert not old_entry.exists()
    assert not list(state_dir.rglob("replacement.marker"))


def test_pair_keyring_write_failure_preserves_recoverable_old_state(
    tmp_path: Path,
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    flow, old_entry = _install_old_submit(
        tmp_path=tmp_path,
        app=app,
        runner=runner,
        memory_keyring=memory_keyring,
        state_dir=state_dir,
        respx_mock=respx_mock,
    )
    _pair_route(respx_mock)
    memory_keyring.write_error = RuntimeError("keyring-write-private-detail")

    paired = _run_pair(app, runner)

    assert paired.exit_code == 3
    assert memory_keyring.values[(SERVICE, USERNAME)] == "refresh-1"
    assert old_entry.exists()
    _marker_document(state_dir)
    combined = f"{paired.stdout}\n{paired.stderr}"
    for secret in ("keyring-write-private-detail", "new-refresh", "new-access"):
        assert secret not in combined
    _assert_state_has_no_secrets(state_dir)
    memory_keyring.write_error = None

    recovered = runner.invoke(app, ["retry", "--server", ORIGIN])

    assert recovered.exit_code == 0
    assert flow.submit_route.call_count == 2
    assert not old_entry.exists()
    assert not list(state_dir.rglob("replacement.marker"))


def test_old_outbox_delete_failure_never_sends_old_job_with_new_identity(
    tmp_path: Path,
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    flow, old_entry = _install_old_submit(
        tmp_path=tmp_path / "old",
        app=app,
        runner=runner,
        memory_keyring=memory_keyring,
        state_dir=state_dir,
        respx_mock=respx_mock,
    )
    _pair_route(respx_mock)
    real_unlink = Path.unlink

    def fail_old_unlink(path: Path, missing_ok: bool = False) -> None:
        if path.resolve() == old_entry.resolve():
            raise OSError("old-outbox-delete-private-detail")
        real_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fail_old_unlink)

    paired = _run_pair(app, runner)

    assert paired.exit_code == 2
    assert memory_keyring.values[(SERVICE, USERNAME)] == "new-refresh"
    assert old_entry.exists()
    _marker_document(state_dir)
    assert flow.submit_route.call_count == 1
    assert "old-outbox-delete-private-detail" not in f"{paired.stdout}\n{paired.stderr}"
    _assert_state_has_no_secrets(state_dir)
    monkeypatch.setattr(Path, "unlink", real_unlink)
    new_manifest = _configure_new_submit(
        flow,
        tmp_path=tmp_path / "new",
        memory_keyring=memory_keyring,
    )

    submitted = runner.invoke(
        app,
        ["submit", "--server", ORIGIN, "--manifest", str(new_manifest)],
    )

    assert submitted.exit_code == 6
    assert flow.submit_route.call_count == 1
    assert flow.create_route.call_count == 2
    assert not list(state_dir.rglob("replacement.marker"))


def test_marker_delete_failure_is_recovered_before_new_remote_mutation(
    tmp_path: Path,
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    flow, old_entry = _install_old_submit(
        tmp_path=tmp_path / "old",
        app=app,
        runner=runner,
        memory_keyring=memory_keyring,
        state_dir=state_dir,
        respx_mock=respx_mock,
    )
    _pair_route(respx_mock)
    real_unlink = Path.unlink

    def fail_marker_unlink(path: Path, missing_ok: bool = False) -> None:
        if path.name == "replacement.marker":
            raise OSError("marker-delete-private-detail")
        real_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fail_marker_unlink)

    paired = _run_pair(app, runner)

    assert paired.exit_code == 2
    assert not old_entry.exists()
    _marker_document(state_dir)
    assert memory_keyring.values[(SERVICE, USERNAME)] == "new-refresh"
    assert "marker-delete-private-detail" not in f"{paired.stdout}\n{paired.stderr}"
    _assert_state_has_no_secrets(state_dir)
    monkeypatch.setattr(Path, "unlink", real_unlink)
    new_manifest = _configure_new_submit(
        flow,
        tmp_path=tmp_path / "new",
        memory_keyring=memory_keyring,
    )

    submitted = runner.invoke(
        app,
        ["submit", "--server", ORIGIN, "--manifest", str(new_manifest)],
    )

    assert submitted.exit_code == 6
    assert flow.create_route.call_count == 2
    assert flow.submit_route.call_count == 1
    assert not list(state_dir.rglob("replacement.marker"))


def test_pair_refuses_to_abandon_unprinted_evidence(
    tmp_path: Path,
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    flow, entry_path = _install_old_submit(
        tmp_path=tmp_path,
        app=app,
        runner=runner,
        memory_keyring=memory_keyring,
        state_dir=state_dir,
        respx_mock=respx_mock,
    )
    document = json.loads(entry_path.read_text(encoding="utf-8"))
    document["phase"] = "EVIDENCE"
    document["evidence"] = {
        "job_id": str(flow.job_id),
        "status": "SCANNING",
        "result_code": None,
        "submitted_at": "2026-08-10T09:55:00Z",
        "updated_at": "2026-08-10T09:55:00Z",
    }
    entry_path.write_text(json.dumps(document), encoding="utf-8")
    original = entry_path.read_bytes()
    route = _pair_route(respx_mock)

    paired = _run_pair(app, runner)

    assert paired.exit_code == 2
    assert route.call_count == 0
    assert "retry" in f"{paired.stdout}\n{paired.stderr}".lower()
    assert entry_path.read_bytes() == original
    assert memory_keyring.values[(SERVICE, USERNAME)] == "refresh-1"
    _assert_state_has_no_secrets(state_dir)


@pytest.mark.parametrize("command", ["pair", "status", "submit", "retry"])
def test_all_same_origin_commands_share_one_bounded_lock_before_remote_mutation(
    command: str,
    tmp_path: Path,
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    outbox_module = importlib.import_module("superboss_connector.outbox")
    store = outbox_module.OutboxStore(ORIGIN)
    memory_keyring.values[(SERVICE, USERNAME)] = "lock-refresh"
    status_job_id = uuid4()
    respx_mock.post(f"{ORIGIN}/api/v1/device-auth/pair").mock(
        return_value=httpx.Response(
            200,
            json=token_payload(access="lock-pair-access", refresh="lock-pair-refresh"),
        )
    )
    respx_mock.post(f"{ORIGIN}/api/v1/device-auth/refresh").mock(
        return_value=httpx.Response(
            200,
            json=token_payload(access="lock-status-access", refresh="lock-status-refresh"),
        )
    )
    respx_mock.get(f"{ORIGIN}/api/v1/device/import-jobs/{status_job_id}").mock(
        return_value=httpx.Response(503)
    )
    manifest, _attachment, _payload = write_manifest(tmp_path / "lock")
    arguments = {
        "pair": [
            "pair",
            "--server",
            ORIGIN,
            "--code",
            "lock-code",
            "--name",
            "Lock-PC",
        ],
        "status": ["status", "--server", ORIGIN, "--job-id", str(status_job_id)],
        "submit": ["submit", "--server", ORIGIN, "--manifest", str(manifest)],
        "retry": ["retry", "--server", ORIGIN],
    }[command]
    keyring_reads = len(memory_keyring.get_calls)
    keyring_writes = len(memory_keyring.set_calls)

    with store.lock():
        result = runner.invoke(app, arguments)

    assert result.exit_code == 2
    assert len(memory_keyring.get_calls) == keyring_reads
    assert len(memory_keyring.set_calls) == keyring_writes
    assert len(respx_mock.calls) == 0


def test_status_rotation_paused_before_save_prevents_concurrent_pair_remote_mutation(
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    load_app(monkeypatch, state_dir)
    cli_module = importlib.import_module("superboss_connector.cli")
    monkeypatch.setattr(cli_module.typer, "echo", lambda *_args, **_kwargs: None)
    job_id = uuid4()
    memory_keyring.values[(SERVICE, USERNAME)] = "refresh-r0"
    status_before_save = threading.Event()
    release_status = threading.Event()
    real_set = memory_keyring.set_password

    def pausing_set(service: str, username: str, password: str) -> None:
        if password == "refresh-status-r1":
            status_before_save.set()
            if not release_status.wait(timeout=5):
                raise RuntimeError("barrier-timeout")
        real_set(service, username, password)

    monkeypatch.setattr(memory_keyring, "set_password", pausing_set)
    respx_mock.post(f"{ORIGIN}/api/v1/device-auth/refresh").mock(
        return_value=httpx.Response(
            200,
            json=token_payload(access="status-access", refresh="refresh-status-r1"),
        )
    )
    status_body = job_payload(
        {
            "project_id": str(uuid4()),
            "local_task_id": "locking-status",
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
    status_route = respx_mock.get(f"{ORIGIN}/api/v1/device/import-jobs/{job_id}").mock(
        return_value=httpx.Response(200, json=status_body)
    )
    pair_route = _pair_route(respx_mock)
    status_result: dict[str, object] = {}

    def run_status() -> None:
        try:
            cli_module.status(server=ORIGIN, job_id=job_id)
            status_result["exit_code"] = 0
        except typer.Exit as error:
            status_result["exit_code"] = error.exit_code
        except BaseException as error:  # noqa: BLE001 - propagate thread failure below
            status_result["error"] = error

    thread = threading.Thread(target=run_status, daemon=True)
    thread.start()
    assert status_before_save.wait(timeout=5)
    try:
        try:
            cli_module.pair(
                server=ORIGIN,
                code="one-time-pair",
                name="Replacement-PC",
            )
            pair_exit = 0
        except typer.Exit as error:
            pair_exit = error.exit_code
    finally:
        release_status.set()
        thread.join(timeout=5)

    assert not thread.is_alive()
    assert "error" not in status_result
    assert status_result["exit_code"] == 0
    assert pair_exit == 2
    assert pair_route.call_count == 0
    assert status_route.call_count == 1
    assert memory_keyring.values[(SERVICE, USERNAME)] == "refresh-status-r1"


def test_lock_for_origin_a_does_not_block_pair_for_origin_b(
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    outbox_module = importlib.import_module("superboss_connector.outbox")
    origin_b = "https://independent-origin.example"
    route = respx_mock.post(f"{origin_b}/api/v1/device-auth/pair").mock(
        return_value=httpx.Response(
            200,
            json=token_payload(access="origin-b-access", refresh="origin-b-refresh"),
        )
    )

    with outbox_module.OutboxStore(ORIGIN).lock():
        result = runner.invoke(
            app,
            [
                "pair",
                "--server",
                origin_b,
                "--code",
                "origin-b-code",
                "--name",
                "Origin-B-PC",
            ],
        )

    assert result.exit_code == 0
    assert route.call_count == 1
    assert (
        memory_keyring.values[(f"SuperBoss/KimiConnector/{origin_b}", USERNAME)]
        == "origin-b-refresh"
    )
