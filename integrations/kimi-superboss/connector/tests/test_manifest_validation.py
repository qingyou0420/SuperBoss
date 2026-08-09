from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
import respx
from conftest import (
    ORIGIN,
    ManifestMutation,
    MemoryKeyring,
    load_app,
    rewrite_manifest,
    write_manifest,
)
from typer.testing import CliRunner


def _extra_root(payload: dict[str, Any], _attachment: Path) -> None:
    payload["unexpected"] = "field"


def _naive_time(payload: dict[str, Any], _attachment: Path) -> None:
    payload["k3_result"]["processed_at"] = "2026-08-10T17:54:00"


def _unsafe_control(payload: dict[str, Any], _attachment: Path) -> None:
    payload["local_task_id"] = "unsafe\u0000task"


def _missing_k3_raw(payload: dict[str, Any], _attachment: Path) -> None:
    payload["attachments"][0]["kind"] = "REVISED"


def _caller_digest(payload: dict[str, Any], _attachment: Path) -> None:
    payload["attachments"][0]["sha256"] = "0" * 64


def _oversized_canonical_json(payload: dict[str, Any], _attachment: Path) -> None:
    payload["k3_result"]["modification_details"] = ["界" * 4096 for _ in range(6)]


def _missing_file(payload: dict[str, Any], _attachment: Path) -> None:
    payload["attachments"][0]["path"] = "missing.json"


def _duplicate_resolved_file(payload: dict[str, Any], attachment: Path) -> None:
    payload["attachments"].append(
        {
            "kind": "REVISED",
            "path": attachment.name,
            "content_type": "application/json",
        }
    )


@pytest.mark.parametrize(
    "mutation",
    [
        _extra_root,
        _naive_time,
        _unsafe_control,
        _missing_k3_raw,
        _caller_digest,
        _oversized_canonical_json,
        _missing_file,
        _duplicate_resolved_file,
    ],
    ids=lambda value: value.__name__,
)
def test_strict_manifest_rejected_before_credential_or_network_access(
    mutation: ManifestMutation,
    tmp_path: Path,
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    manifest, attachment, original = write_manifest(tmp_path / "input")
    payload = deepcopy(original)
    mutation(payload, attachment)
    rewrite_manifest(manifest, payload)

    result = runner.invoke(
        app,
        ["submit", "--server", ORIGIN, "--manifest", str(manifest)],
    )

    assert result.exit_code == 2
    assert memory_keyring.get_calls == []
    assert memory_keyring.set_calls == []
    assert len(respx_mock.calls) == 0
    assert "refresh" not in f"{result.stdout}\n{result.stderr}".lower()


def test_empty_attachment_is_rejected_before_hashing_credentials_or_network(
    tmp_path: Path,
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    manifest, _attachment, _payload = write_manifest(tmp_path / "input", content=b"")

    result = runner.invoke(
        app,
        ["submit", "--server", ORIGIN, "--manifest", str(manifest)],
    )

    assert result.exit_code == 2
    assert memory_keyring.get_calls == []
    assert len(respx_mock.calls) == 0


@pytest.mark.parametrize("invalid_file", ["directory", "over_100_mib"])
def test_non_regular_or_oversized_attachment_exits_2_before_credentials_or_network(
    invalid_file: str,
    tmp_path: Path,
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    manifest, _attachment, payload = write_manifest(tmp_path / "input")
    if invalid_file == "directory":
        invalid_path = manifest.parent / "attachment-directory"
        invalid_path.mkdir()
    else:
        invalid_path = manifest.parent / "oversized.bin"
        with invalid_path.open("wb") as stream:
            stream.seek(100 * 1024 * 1024)
            stream.write(b"X")
        assert invalid_path.stat().st_size == 100 * 1024 * 1024 + 1
    payload["attachments"][0]["path"] = invalid_path.name
    payload["attachments"][0]["content_type"] = "application/octet-stream"
    rewrite_manifest(manifest, payload)

    result = runner.invoke(
        app,
        ["submit", "--server", ORIGIN, "--manifest", str(manifest)],
    )

    assert result.exit_code == 2
    assert memory_keyring.get_calls == []
    assert memory_keyring.set_calls == []
    assert len(respx_mock.calls) == 0
