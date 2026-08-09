from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import respx
from conftest import (
    ORIGIN,
    USERNAME,
    MemoryKeyring,
    install_response_loss_flow,
    load_app,
    token_payload,
    write_manifest,
)
from typer.testing import CliRunner


@pytest.mark.parametrize(
    "origin",
    [
        "https:// /",
        "https://exa mple.com",
        "https://example%2ecom",
        "https://example.com\\@evil.test",
        "https://example.com:0",
        "https://-bad.example",
        "https://bad-.example",
        "https://a..example",
        f"https://{'a' * 64}.example",
        "https://under_score.example",
        "https://127.1",
        "https://2130706433",
        "https://\ud800.example",
    ],
)
def test_invalid_authority_exits_2_before_credentials_or_network(
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
        ["status", "--server", origin, "--job-id", str(uuid4())],
    )

    assert result.exit_code == 2
    assert memory_keyring.get_calls == []
    assert memory_keyring.set_calls == []
    assert len(respx_mock.calls) == 0


@pytest.mark.parametrize(
    ("raw_origin", "canonical_origin"),
    [
        ("https://B\u00dcCHER.Example.:443/", "https://xn--bcher-kva.example"),
        ("https://EXAMPLE.COM.:443", "https://example.com"),
        ("http://LOCALHOST.:80/", "http://localhost"),
        ("http://127.0.0.1:80/", "http://127.0.0.1"),
        ("http://[::1]:80/", "http://[::1]"),
    ],
)
def test_origin_idna_trailing_dot_and_default_port_are_canonical(
    raw_origin: str,
    canonical_origin: str,
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    route = respx_mock.post(f"{canonical_origin}/api/v1/device-auth/pair").mock(
        return_value=httpx.Response(
            200,
            json=token_payload(access="canonical-access", refresh="canonical-refresh"),
        )
    )
    if raw_origin.startswith("https://") and ".:" in raw_origin:
        respx_mock.post(f"{canonical_origin}./api/v1/device-auth/pair").mock(
            return_value=httpx.Response(
                200,
                json=token_payload(access="legacy-access", refresh="legacy-refresh"),
            )
        )

    result = runner.invoke(
        app,
        ["pair", "--server", raw_origin, "--code", "one-time", "--name", "Owner-PC"],
    )

    assert result.exit_code == 0
    assert route.call_count == 1
    assert memory_keyring.values == {
        (f"SuperBoss/KimiConnector/{canonical_origin}", USERNAME): "canonical-refresh"
    }


@pytest.mark.parametrize(
    "idempotency_key",
    [
        " ",
        "kimi task",
        " leading",
        "trailing ",
        "tab\tkey",
        "line\nkey",
        "delete\x7fkey",
        "\u975eascii",
        "surrogate\ud800key",
    ],
)
def test_invalid_raw_idempotency_key_exits_2_without_any_side_effect(
    idempotency_key: str,
    tmp_path: Path,
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    manifest, _attachment, payload = write_manifest(tmp_path / "invalid-key")
    payload["idempotency_key"] = idempotency_key
    manifest.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")

    result = runner.invoke(
        app,
        ["submit", "--server", ORIGIN, "--manifest", str(manifest)],
    )

    assert result.exit_code == 2
    assert not list(state_dir.rglob("*.json"))
    assert memory_keyring.get_calls == []
    assert memory_keyring.set_calls == []
    assert len(respx_mock.calls) == 0


@pytest.mark.parametrize("idempotency_key", ["!", "~", "!~", "!" * 255])
def test_printable_idempotency_boundaries_are_preserved_byte_for_byte(
    idempotency_key: str,
    tmp_path: Path,
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    manifest, _attachment, _payload = write_manifest(
        tmp_path / "valid-key",
        idempotency_key=idempotency_key,
    )
    service = f"SuperBoss/KimiConnector/{ORIGIN}"
    memory_keyring.values[(service, USERNAME)] = "boundary-refresh"
    respx_mock.post(f"{ORIGIN}/api/v1/device-auth/refresh").mock(
        return_value=httpx.Response(
            200,
            json=token_payload(access="boundary-access", refresh="boundary-rotated"),
        )
    )
    create = respx_mock.post(f"{ORIGIN}/api/v1/device/import-jobs").mock(
        return_value=httpx.Response(503)
    )

    result = runner.invoke(
        app,
        ["submit", "--server", ORIGIN, "--manifest", str(manifest)],
    )

    assert result.exit_code == 6
    assert create.calls[0].request.headers["Idempotency-Key"] == idempotency_key
    entry = json.loads(next(state_dir.rglob("*.json")).read_text(encoding="utf-8"))
    assert entry["idempotency_key"] == idempotency_key


def test_corrupt_origin_a_does_not_block_origin_b(
    tmp_path: Path,
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    origin_a = "https://origin-a.example"
    origin_b = "https://origin-b.example"
    origin_a_hash = hashlib.sha256(origin_a.encode("utf-8")).hexdigest()
    corrupt = state_dir / origin_a_hash / "corrupt.json"
    corrupt.parent.mkdir(parents=True)
    corrupt.write_text("{not-json", encoding="utf-8")
    original_corrupt = corrupt.read_bytes()
    manifest, _attachment, _payload = write_manifest(
        tmp_path / "origin-b",
        idempotency_key="origin-b-new-key",
    )
    service_b = f"SuperBoss/KimiConnector/{origin_b}"
    memory_keyring.values[(service_b, USERNAME)] = "origin-b-refresh"
    refresh = respx_mock.post(f"{origin_b}/api/v1/device-auth/refresh").mock(
        return_value=httpx.Response(
            200,
            json=token_payload(access="origin-b-access", refresh="origin-b-rotated"),
        )
    )
    create = respx_mock.post(f"{origin_b}/api/v1/device/import-jobs").mock(
        return_value=httpx.Response(503)
    )

    result = runner.invoke(
        app,
        ["submit", "--server", origin_b, "--manifest", str(manifest)],
    )

    assert result.exit_code == 6
    assert refresh.call_count == 1
    assert create.call_count == 1
    assert corrupt.read_bytes() == original_corrupt


@pytest.mark.parametrize("tamper", ["filename", "key", "origin_path"])
def test_origin_local_scan_still_validates_filename_key_and_origin_path_integrity(
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
        tmp_path / tamper,
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
    if tamper == "filename":
        entry_path = entry_path.rename(entry_path.with_name(f"{'0' * 64}.json"))
    elif tamper == "key":
        document["idempotency_key"] = "different-valid-key"
        entry_path.write_text(json.dumps(document), encoding="utf-8")
    else:
        document["normalized_origin"] = "https://different-origin.example"
        entry_path.write_text(json.dumps(document), encoding="utf-8")
    calls_before = len(respx_mock.calls)
    keyring_reads_before = len(memory_keyring.get_calls)

    retried = runner.invoke(app, ["retry", "--server", ORIGIN])

    assert retried.exit_code == 2
    assert len(respx_mock.calls) == calls_before
    assert len(memory_keyring.get_calls) == keyring_reads_before
    assert entry_path.exists()
