from __future__ import annotations

import hashlib
import importlib
import json
import os
import time
from pathlib import Path
from typing import Any

import pytest
import respx
from conftest import (
    ORIGIN,
    MemoryKeyring,
    install_response_loss_flow,
    load_app,
)
from typer.testing import CliRunner


def _corrupt_state_path(state_dir: Path, *, marker: bool) -> Path:
    origin_hash = hashlib.sha256(ORIGIN.encode("utf-8")).hexdigest()
    origin_dir = state_dir / origin_hash
    origin_dir.mkdir(parents=True, exist_ok=True)
    return origin_dir / ("replacement.marker" if marker else f"{'0' * 64}.json")


@pytest.mark.parametrize("marker", [False, True], ids=["outbox", "marker"])
def test_deep_local_state_json_is_stable_input_error_before_credentials_or_network(
    marker: bool,
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    path = _corrupt_state_path(state_dir, marker=marker)
    path.write_bytes(b"[" * 10_000 + b"]" * 10_000)
    keyring_reads = len(memory_keyring.get_calls)
    arguments = (
        ["pair", "--server", ORIGIN, "--code", "local-code", "--name", "PC"]
        if marker
        else ["retry", "--server", ORIGIN]
    )

    result = runner.invoke(app, arguments)

    assert result.exit_code == 2
    assert len(memory_keyring.get_calls) == keyring_reads
    assert len(memory_keyring.set_calls) == 0
    assert len(respx_mock.calls) == 0
    assert str(path) not in f"{result.stdout}\n{result.stderr}"


@pytest.mark.parametrize("marker", [False, True], ids=["outbox", "marker"])
def test_oversized_local_state_is_rejected_without_parsing_or_external_access(
    marker: bool,
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    outbox_module = importlib.import_module("superboss_connector.outbox")
    path = _corrupt_state_path(state_dir, marker=marker)
    path.write_bytes(b" " * (outbox_module.LOCAL_STATE_MAX_BYTES + 1))

    def parsing_is_forbidden(_value: object) -> object:
        pytest.fail("oversized local state reached JSON parsing")

    monkeypatch.setattr(outbox_module.json, "loads", parsing_is_forbidden)
    keyring_reads = len(memory_keyring.get_calls)
    arguments = (
        ["pair", "--server", ORIGIN, "--code", "local-code", "--name", "PC"]
        if marker
        else ["retry", "--server", ORIGIN]
    )

    result = runner.invoke(app, arguments)

    assert result.exit_code == 2
    assert len(memory_keyring.get_calls) == keyring_reads
    assert len(memory_keyring.set_calls) == 0
    assert len(respx_mock.calls) == 0


def test_same_origin_windows_lock_contention_fails_bounded_without_state_damage(
    tmp_path: Path,
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    flow = install_response_loss_flow(
        tmp_path / "lock",
        memory_keyring,
        respx_mock,
        loss_stage="create",
    )
    first = runner.invoke(
        app,
        ["submit", "--server", ORIGIN, "--manifest", str(flow.manifest)],
    )
    assert first.exit_code == 6
    entries = list(state_dir.rglob("*.json"))
    assert len(entries) == 1
    original = entries[0].read_bytes()
    calls_before = len(respx_mock.calls)
    outbox_module = importlib.import_module("superboss_connector.outbox")
    store = outbox_module.OutboxStore(ORIGIN)

    started = time.monotonic()
    with store.lock():
        contended = runner.invoke(app, ["retry", "--server", ORIGIN])
    elapsed = time.monotonic() - started

    assert contended.exit_code == 2
    assert elapsed < 5.0
    assert len(respx_mock.calls) == calls_before
    assert entries[0].read_bytes() == original
    json.loads(original)
    combined = f"{contended.stdout}\n{contended.stderr}"
    for secret in ("refresh-0", "refresh-1", "access-1", "storage.local"):
        assert secret not in combined


def test_atomic_replace_failure_preserves_last_checkpoint_and_cleans_temp(
    tmp_path: Path,
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    flow = install_response_loss_flow(
        tmp_path / "replace",
        memory_keyring,
        respx_mock,
        loss_stage="create",
    )
    first = runner.invoke(
        app,
        ["submit", "--server", ORIGIN, "--manifest", str(flow.manifest)],
    )
    assert first.exit_code == 6
    entries = list(state_dir.rglob("*.json"))
    assert len(entries) == 1
    entry = entries[0]
    original = entry.read_bytes()
    json.loads(original)

    outbox_module = importlib.import_module("superboss_connector.outbox")
    real_os_replace = os.replace
    real_path_replace = Path.replace

    def replace_fails(source: Any, destination: Any) -> None:
        if Path(destination).resolve() == entry.resolve():
            raise OSError("replace-failure-private-detail")
        real_os_replace(source, destination)

    def path_replace_fails(source: Path, destination: str | Path) -> Path:
        if Path(destination).resolve() == entry.resolve():
            raise OSError("replace-failure-private-detail")
        return real_path_replace(source, destination)

    monkeypatch.setattr(outbox_module.os, "replace", replace_fails)
    monkeypatch.setattr(Path, "replace", path_replace_fails)

    retried = runner.invoke(app, ["retry", "--server", ORIGIN])

    assert retried.exit_code == 2
    assert flow.create_route.call_count == 2
    assert flow.accepted["child_allocations"] == 1
    assert flow.part_route.call_count == 0
    assert flow.put_route.call_count == 0
    assert entry.read_bytes() == original
    json.loads(entry.read_text(encoding="utf-8"))
    temporary_files = [
        path
        for path in state_dir.rglob("*")
        if path.is_file() and path != entry and path.suffix.lower() in {".tmp", ".temp"}
    ]
    assert temporary_files == []
    combined = f"{retried.stdout}\n{retried.stderr}"
    for secret in (
        "replace-failure-private-detail",
        "refresh-1",
        "refresh-2",
        "access-2",
    ):
        assert secret not in combined


@pytest.mark.parametrize(
    "corruption",
    ["part_size", "duplicate_part", "oversized_file", "create_with_completed_part"],
)
def test_structurally_corrupt_outbox_fails_before_credentials_or_network(
    corruption: str,
    tmp_path: Path,
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    flow = install_response_loss_flow(
        tmp_path / corruption,
        memory_keyring,
        respx_mock,
        loss_stage="complete",
    )
    first = runner.invoke(
        app,
        ["submit", "--server", ORIGIN, "--manifest", str(flow.manifest)],
    )
    assert first.exit_code == 6
    entry = next(state_dir.rglob("*.json"))
    document = json.loads(entry.read_text(encoding="utf-8"))
    if corruption == "part_size":
        document["attachments"][0]["part_size"] = 0
    elif corruption == "duplicate_part":
        document["attachments"][0]["completed_parts"].append(
            dict(document["attachments"][0]["completed_parts"][0])
        )
    elif corruption == "oversized_file":
        document["attachments"][0]["size_bytes"] = 100 * 1024 * 1024 + 1
    else:
        document["phase"] = "CREATE"
        document["job_id"] = None
        document["attachments"][0]["attachment_id"] = None
        document["attachments"][0]["file_id"] = None
        document["attachments"][0]["upload_id"] = None
    entry.write_text(json.dumps(document), encoding="utf-8")
    calls_before = len(respx_mock.calls)
    keyring_calls_before = len(memory_keyring.get_calls)

    retried = runner.invoke(app, ["retry", "--server", ORIGIN])

    assert retried.exit_code == 2
    assert len(respx_mock.calls) == calls_before
    assert len(memory_keyring.get_calls) == keyring_calls_before


def test_exit_4_cleanup_delete_failure_is_stable_and_secret_safe(
    tmp_path: Path,
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    flow = install_response_loss_flow(
        tmp_path / "delete-failure",
        memory_keyring,
        respx_mock,
        loss_stage="create",
    )
    first = runner.invoke(
        app,
        ["submit", "--server", ORIGIN, "--manifest", str(flow.manifest)],
    )
    assert first.exit_code == 6
    entry = next(state_dir.rglob("*.json"))
    flow.attachment.write_bytes(b"changed-before-retry")
    calls_before = len(respx_mock.calls)
    keyring_calls_before = len(memory_keyring.get_calls)
    real_unlink = Path.unlink

    def unlink_fails(path: Path, missing_ok: bool = False) -> None:
        if path.resolve() == entry.resolve():
            raise OSError("delete-failure-private-detail")
        real_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", unlink_fails)

    retried = runner.invoke(app, ["retry", "--server", ORIGIN])

    assert retried.exit_code == 2
    assert len(respx_mock.calls) == calls_before
    assert len(memory_keyring.get_calls) == keyring_calls_before
    assert entry.exists()
    combined = f"{retried.stdout}\n{retried.stderr}"
    assert "delete-failure-private-detail" not in combined
    assert str(entry) not in combined
