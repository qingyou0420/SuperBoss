from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
import respx
from conftest import ORIGIN, MemoryKeyring, install_response_loss_flow, load_app
from typer.testing import CliRunner


def test_broken_output_after_submit_ack_is_recoverable_without_any_remote_replay(
    tmp_path: Path,
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    flow = install_response_loss_flow(
        tmp_path / "broken-output",
        memory_keyring,
        respx_mock,
        loss_stage="create",
    )
    first = runner.invoke(
        app,
        ["submit", "--server", ORIGIN, "--manifest", str(flow.manifest)],
    )
    assert first.exit_code == 6
    cli_module = importlib.import_module("superboss_connector.cli")
    real_print = cli_module._print_result

    def broken_output(_result: Any) -> None:
        raise BrokenPipeError("console-private-detail")

    monkeypatch.setattr(cli_module, "_print_result", broken_output)

    acknowledged = runner.invoke(app, ["retry", "--server", ORIGIN])

    assert acknowledged.exit_code == 2
    entry_path = next(state_dir.rglob("*.json"))
    evidence_state = json.loads(entry_path.read_text(encoding="utf-8"))
    assert evidence_state["phase"] == "EVIDENCE"
    assert evidence_state["evidence"] == {
        "job_id": str(flow.job_id),
        "status": "SCANNING",
        "result_code": None,
        "submitted_at": "2026-08-10T09:55:00Z",
        "updated_at": "2026-08-10T09:55:00Z",
    }
    calls_after_ack = len(respx_mock.calls)
    keyring_reads_after_ack = len(memory_keyring.get_calls)
    put_calls_after_ack = flow.put_route.call_count
    flow.manifest.unlink()
    flow.attachment.unlink()
    monkeypatch.setattr(cli_module, "_print_result", real_print)

    recovered = runner.invoke(app, ["retry", "--server", ORIGIN])

    assert recovered.exit_code == 0
    assert str(flow.job_id) in recovered.stdout and "SCANNING" in recovered.stdout
    assert len(respx_mock.calls) == calls_after_ack
    assert len(memory_keyring.get_calls) == keyring_reads_after_ack
    assert flow.put_route.call_count == put_calls_after_ack
    assert not list(state_dir.rglob("*.json"))
    combined = f"{acknowledged.stdout}\n{acknowledged.stderr}\n{recovered.stdout}"
    for secret in ("console-private-detail", "access-2", "refresh-2", "stable-etag"):
        assert secret not in combined


def test_crash_between_submit_ack_and_evidence_checkpoint_replays_only_submit(
    tmp_path: Path,
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    flow = install_response_loss_flow(
        tmp_path / "evidence-save-crash",
        memory_keyring,
        respx_mock,
        loss_stage="create",
    )
    first = runner.invoke(
        app,
        ["submit", "--server", ORIGIN, "--manifest", str(flow.manifest)],
    )
    assert first.exit_code == 6
    outbox_module = importlib.import_module("superboss_connector.outbox")
    real_replace = os.replace

    def fail_evidence_replace(source: str | bytes | Path, destination: str | bytes | Path) -> None:
        source_path = Path(source)
        if source_path.exists() and b'"phase":"EVIDENCE"' in source_path.read_bytes():
            raise OSError("evidence-checkpoint-private-detail")
        real_replace(source, destination)

    monkeypatch.setattr(outbox_module.os, "replace", fail_evidence_replace)

    acknowledged = runner.invoke(app, ["retry", "--server", ORIGIN])

    assert acknowledged.exit_code == 2
    entry_path = next(state_dir.rglob("*.json"))
    submit_state = json.loads(entry_path.read_text(encoding="utf-8"))
    assert submit_state["phase"] == "SUBMIT"
    assert flow.submit_route.call_count == 1
    put_calls_after_ack = flow.put_route.call_count
    monkeypatch.setattr(outbox_module.os, "replace", real_replace)

    recovered = runner.invoke(app, ["retry", "--server", ORIGIN])

    assert recovered.exit_code == 0
    assert flow.submit_route.call_count == 2
    assert flow.put_route.call_count == put_calls_after_ack
    assert str(flow.job_id) in recovered.stdout
    assert not list(state_dir.rglob("*.json"))
    assert "evidence-checkpoint-private-detail" not in (
        f"{acknowledged.stdout}\n{acknowledged.stderr}"
    )


@pytest.mark.parametrize("tamper", ["status", "job_id", "naive_time", "extra"])
def test_tampered_evidence_checkpoint_exits_2_before_credentials_or_network(
    tamper: str,
    tmp_path: Path,
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    flow = install_response_loss_flow(
        tmp_path / "tampered-evidence",
        memory_keyring,
        respx_mock,
        loss_stage="submit",
    )
    first = runner.invoke(
        app,
        ["submit", "--server", ORIGIN, "--manifest", str(flow.manifest)],
    )
    assert first.exit_code == 6
    entry_path = next(state_dir.rglob("*.json"))
    document = json.loads(entry_path.read_text(encoding="utf-8"))
    document["phase"] = "EVIDENCE"
    evidence = {
        "job_id": document["job_id"],
        "status": "SCANNING",
        "result_code": None,
        "submitted_at": "2026-08-10T09:55:00Z",
        "updated_at": "2026-08-10T09:55:00Z",
    }
    if tamper == "status":
        evidence["status"] = "UPLOADING"
    elif tamper == "job_id":
        evidence["job_id"] = str(uuid4())
    elif tamper == "naive_time":
        evidence["updated_at"] = "2026-08-10T09:55:00"
    else:
        evidence["unexpected"] = "not-allowed"
    document["evidence"] = evidence
    entry_path.write_text(json.dumps(document), encoding="utf-8")
    calls_before = len(respx_mock.calls)
    keyring_reads_before = len(memory_keyring.get_calls)

    retried = runner.invoke(app, ["retry", "--server", ORIGIN])

    assert retried.exit_code == 2
    assert len(respx_mock.calls) == calls_before
    assert len(memory_keyring.get_calls) == keyring_reads_before
    assert entry_path.exists()
