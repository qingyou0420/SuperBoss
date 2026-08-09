from __future__ import annotations

import hashlib
import importlib
import json
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
    attachment_payload,
    expected_server_manifest,
    job_payload,
    load_app,
    token_payload,
    write_manifest,
)
from typer.testing import CliRunner

PART_SIZE = 8 * 1024 * 1024


@dataclass
class InterruptedFlow:
    manifest: Path
    attachment: Path
    idempotency_key: str
    job_id: UUID
    attachment_id: UUID
    refresh_route: Any
    create_route: Any
    part_routes: dict[int, Any]
    put_routes: dict[int, Any]
    complete_route: Any
    submit_route: Any


def _assert_new_key_submit_reaches_create(
    *,
    app: Any,
    runner: CliRunner,
    state_dir: Path,
    directory: Path,
    create_route: Any,
    idempotency_key: str,
) -> None:
    create_calls_before = create_route.call_count
    create_route.mock(return_value=httpx.Response(503))
    manifest, _attachment, _payload = write_manifest(
        directory,
        idempotency_key=idempotency_key,
    )

    result = runner.invoke(
        app,
        ["submit", "--server", ORIGIN, "--manifest", str(manifest)],
    )

    assert result.exit_code == 6
    assert create_route.call_count == create_calls_before + 1
    entries = list(state_dir.rglob("*.json"))
    assert len(entries) == 1
    assert json.loads(entries[0].read_text(encoding="utf-8"))["idempotency_key"] == idempotency_key


def _install_interrupted_flow(
    tmp_path: Path,
    memory_keyring: MemoryKeyring,
    respx_mock: respx.MockRouter,
) -> InterruptedFlow:
    manifest, attachment, payload = write_manifest(
        tmp_path / "input",
        content=b"A" * (PART_SIZE * 2) + b"Z",
        idempotency_key="stable-resume-key",
        filename="large-k3-result.json",
    )
    server_manifest = expected_server_manifest(payload, attachment)
    job_id = uuid4()
    attachment_id = uuid4()
    file_id = uuid4()
    upload_id = uuid4()
    memory_keyring.values[(SERVICE, USERNAME)] = "refresh-0"

    refresh_attempt = 0

    def refresh_response(request: httpx.Request) -> httpx.Response:
        nonlocal refresh_attempt
        refresh_attempt += 1
        assert json.loads(request.content) == {"refresh_token": f"refresh-{refresh_attempt - 1}"}
        return httpx.Response(
            200,
            json=token_payload(
                access=f"access-{refresh_attempt}",
                refresh=f"refresh-{refresh_attempt}",
            ),
        )

    refresh_route = respx_mock.post(f"{ORIGIN}/api/v1/device-auth/refresh").mock(
        side_effect=refresh_response
    )

    def create_response(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer access-1"
        assert request.headers["Idempotency-Key"] == "stable-resume-key"
        assert json.loads(request.content) == server_manifest
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

    create_route = respx_mock.post(f"{ORIGIN}/api/v1/device/import-jobs").mock(
        side_effect=create_response
    )
    part_routes: dict[int, Any] = {}
    put_routes: dict[int, Any] = {}
    for part_number in (1, 2, 3):
        part_routes[part_number] = respx_mock.post(
            f"{ORIGIN}/api/v1/device/import-jobs/{job_id}/attachments/"
            f"{attachment_id}/parts/{part_number}"
        ).mock(
            return_value=httpx.Response(
                200,
                json={"url": f"https://storage.local/upload/{part_number}"},
            )
        )

    def put_success(part_number: int) -> Any:
        def response(request: httpx.Request) -> httpx.Response:
            assert "Authorization" not in request.headers
            expected_size = PART_SIZE if part_number < 3 else 1
            assert len(request.content) == expected_size
            return httpx.Response(200, headers={"ETag": f"etag-{part_number}"})

        return response

    put_routes[1] = respx_mock.put("https://storage.local/upload/1").mock(
        side_effect=put_success(1)
    )
    put_routes[2] = respx_mock.put("https://storage.local/upload/2").mock(
        side_effect=put_success(2)
    )
    part_three_attempt = 0

    def put_three(request: httpx.Request) -> httpx.Response:
        nonlocal part_three_attempt
        assert "Authorization" not in request.headers
        assert request.content == b"Z"
        part_three_attempt += 1
        if part_three_attempt == 1:
            raise httpx.ConnectError("simulated network loss", request=request)
        return httpx.Response(200, headers={"ETag": "etag-3"})

    put_routes[3] = respx_mock.put("https://storage.local/upload/3").mock(side_effect=put_three)

    def complete_response(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer access-2"
        assert json.loads(request.content) == {
            "parts": [
                {"part_number": 1, "etag": "etag-1"},
                {"part_number": 2, "etag": "etag-2"},
                {"part_number": 3, "etag": "etag-3"},
            ]
        }
        return httpx.Response(
            200,
            json=attachment_payload(
                attachment_id=attachment_id,
                file_id=file_id,
                upload_id=upload_id,
                state="QUARANTINED",
            ),
        )

    complete_route = respx_mock.post(
        f"{ORIGIN}/api/v1/device/import-jobs/{job_id}/attachments/{attachment_id}/complete"
    ).mock(side_effect=complete_response)

    def submit_response(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer access-2"
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

    submit_route = respx_mock.post(f"{ORIGIN}/api/v1/device/import-jobs/{job_id}/submit").mock(
        side_effect=submit_response
    )
    return InterruptedFlow(
        manifest=manifest,
        attachment=attachment,
        idempotency_key="stable-resume-key",
        job_id=job_id,
        attachment_id=attachment_id,
        refresh_route=refresh_route,
        create_route=create_route,
        part_routes=part_routes,
        put_routes=put_routes,
        complete_route=complete_route,
        submit_route=submit_route,
    )


def test_network_loss_after_part_2_retries_at_part_3_with_same_ids_and_key(
    tmp_path: Path,
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    flow = _install_interrupted_flow(tmp_path, memory_keyring, respx_mock)

    first = runner.invoke(
        app,
        ["submit", "--server", ORIGIN, "--manifest", str(flow.manifest)],
    )

    assert first.exit_code == 6
    assert flow.create_route.call_count == 1
    assert flow.put_routes[1].call_count == 1
    assert flow.put_routes[2].call_count == 1
    assert flow.put_routes[3].call_count == 1
    assert not flow.complete_route.called and not flow.submit_route.called

    retried = runner.invoke(app, ["retry", "--server", ORIGIN])

    assert retried.exit_code == 0
    assert flow.refresh_route.call_count == 2
    assert flow.create_route.call_count == 1
    assert flow.part_routes[1].call_count == 1
    assert flow.part_routes[2].call_count == 1
    assert flow.part_routes[3].call_count == 2
    assert flow.put_routes[1].call_count == 1
    assert flow.put_routes[2].call_count == 1
    assert flow.put_routes[3].call_count == 2
    assert flow.complete_route.call_count == 1
    assert flow.submit_route.call_count == 1
    assert str(flow.job_id) in retried.stdout
    assert "SCANNING" in retried.stdout
    assert not list(state_dir.rglob("*.json"))


def test_file_mutation_before_retry_exits_4_without_auth_or_network(
    tmp_path: Path,
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    flow = _install_interrupted_flow(tmp_path, memory_keyring, respx_mock)
    first = runner.invoke(
        app,
        ["submit", "--server", ORIGIN, "--manifest", str(flow.manifest)],
    )
    assert first.exit_code == 6
    call_count = len(respx_mock.calls)
    flow.attachment.write_bytes(flow.attachment.read_bytes()[:-1] + b"X")

    retried = runner.invoke(app, ["retry", "--server", ORIGIN])

    assert retried.exit_code == 4
    assert len(respx_mock.calls) == call_count
    assert flow.refresh_route.call_count == 1
    assert not list(state_dir.rglob("*.json"))
    assert not flow.complete_route.called and not flow.submit_route.called
    _assert_new_key_submit_reaches_create(
        app=app,
        runner=runner,
        state_dir=state_dir,
        directory=tmp_path / "replacement-after-retry-change",
        create_route=flow.create_route,
        idempotency_key="replacement-after-retry-change",
    )


def test_interrupted_outbox_is_hashed_atomic_json_and_contains_no_secrets(
    tmp_path: Path,
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    flow = _install_interrupted_flow(tmp_path, memory_keyring, respx_mock)
    result = runner.invoke(
        app,
        ["submit", "--server", ORIGIN, "--manifest", str(flow.manifest)],
    )
    assert result.exit_code == 6

    entries = list(state_dir.rglob("*.json"))
    assert len(entries) == 1
    entry = entries[0]
    document = json.loads(entry.read_text(encoding="utf-8"))
    assert document["normalized_origin"] == ORIGIN
    assert document["idempotency_key"] == flow.idempotency_key
    assert document["job_id"] == str(flow.job_id)
    assert document["attachments"][0]["completed_parts"] == [
        {"part_number": 1, "etag": "etag-1"},
        {"part_number": 2, "etag": "etag-2"},
    ]
    assert ORIGIN not in entry.name and flow.idempotency_key not in entry.name
    serialized = entry.read_text(encoding="utf-8").lower()
    forbidden = (
        "access-1",
        "refresh-0",
        "refresh-1",
        "authorization",
        "storage.local",
        "presigned",
        "object_key",
        "multipart_id",
    )
    assert all(secret not in serialized for secret in forbidden)
    assert not list(state_dir.rglob("*.tmp"))


def test_second_unfinished_submit_for_same_origin_exits_2_before_auth(
    tmp_path: Path,
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    flow = _install_interrupted_flow(tmp_path, memory_keyring, respx_mock)
    first = runner.invoke(
        app,
        ["submit", "--server", ORIGIN, "--manifest", str(flow.manifest)],
    )
    assert first.exit_code == 6
    second_manifest, _attachment, _payload = write_manifest(
        tmp_path / "second",
        idempotency_key="different-key",
    )
    call_count = len(respx_mock.calls)

    second = runner.invoke(
        app,
        ["submit", "--server", ORIGIN, "--manifest", str(second_manifest)],
    )

    assert second.exit_code == 2
    assert len(respx_mock.calls) == call_count
    assert "retry" in f"{second.stdout}\n{second.stderr}".lower()


@pytest.mark.parametrize("mutation", ["truncate", "rewrite"])
def test_file_mutation_after_presign_exits_4_before_complete_or_submit(
    mutation: str,
    tmp_path: Path,
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    manifest, attachment, payload = write_manifest(
        tmp_path / mutation,
        content=b"original-attachment-content",
    )
    server_manifest = expected_server_manifest(payload, attachment)
    job_id = uuid4()
    attachment_id = uuid4()
    file_id = uuid4()
    upload_id = uuid4()
    memory_keyring.values[(SERVICE, USERNAME)] = "refresh-before-mutation"
    respx_mock.post(f"{ORIGIN}/api/v1/device-auth/refresh").mock(
        return_value=httpx.Response(
            200,
            json=token_payload(access="access-before-mutation", refresh="refresh-after-mutation"),
        )
    )
    create = respx_mock.post(f"{ORIGIN}/api/v1/device/import-jobs").mock(
        return_value=httpx.Response(
            201,
            json=job_payload(
                server_manifest,
                job_id=job_id,
                attachment_id=attachment_id,
                file_id=file_id,
                upload_id=upload_id,
            ),
        )
    )

    def mutate_after_presign(_request: httpx.Request) -> httpx.Response:
        if mutation == "truncate":
            attachment.write_bytes(b"cut")
        else:
            attachment.write_bytes(b"X" * attachment.stat().st_size)
        return httpx.Response(200, json={"url": "https://storage.local/mutated-part"})

    respx_mock.post(
        f"{ORIGIN}/api/v1/device/import-jobs/{job_id}/attachments/{attachment_id}/parts/1"
    ).mock(side_effect=mutate_after_presign)
    put = respx_mock.put("https://storage.local/mutated-part").mock(
        return_value=httpx.Response(200, headers={"ETag": "mutated-etag"})
    )
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

    assert result.exit_code == 4
    assert not complete.called and not submit.called
    assert not put.called
    assert not list(state_dir.rglob("*.json"))
    _assert_new_key_submit_reaches_create(
        app=app,
        runner=runner,
        state_dir=state_dir,
        directory=tmp_path / f"replacement-after-{mutation}",
        create_route=create,
        idempotency_key=f"replacement-after-{mutation}",
    )


def test_same_size_rewrite_after_verify_before_read_sends_zero_parts(
    tmp_path: Path,
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    flow = _install_interrupted_flow(tmp_path, memory_keyring, respx_mock)
    cli_module = importlib.import_module("superboss_connector.cli")
    real_verify = cli_module.verify_attachment
    verification_count = 0

    def verify_then_rewrite(path: Path, expected_size: int, expected_sha256: str) -> None:
        nonlocal verification_count
        real_verify(path, expected_size, expected_sha256)
        verification_count += 1
        if verification_count == 2:
            path.write_bytes(b"X" * expected_size)

    monkeypatch.setattr(cli_module, "verify_attachment", verify_then_rewrite)

    result = runner.invoke(
        app,
        ["submit", "--server", ORIGIN, "--manifest", str(flow.manifest)],
    )

    assert result.exit_code == 4
    assert verification_count >= 2
    assert all(route.call_count == 0 for route in flow.put_routes.values())
    assert flow.complete_route.call_count == 0
    assert flow.submit_route.call_count == 0
    assert not list(state_dir.rglob("*.json"))


def test_outbox_persists_approved_digest_for_every_part_and_resume_keeps_them(
    tmp_path: Path,
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    flow = _install_interrupted_flow(tmp_path, memory_keyring, respx_mock)
    expected_digests = [
        hashlib.sha256(block).hexdigest()
        for block in (
            b"A" * PART_SIZE,
            b"A" * PART_SIZE,
            b"Z",
        )
    ]

    first = runner.invoke(
        app,
        ["submit", "--server", ORIGIN, "--manifest", str(flow.manifest)],
    )

    assert first.exit_code == 6
    entry_path = next(state_dir.rglob("*.json"))
    first_state = json.loads(entry_path.read_text(encoding="utf-8"))
    assert first_state["attachments"][0]["part_sha256s"] == expected_digests

    retried = runner.invoke(app, ["retry", "--server", ORIGIN])

    assert retried.exit_code == 0
    assert flow.put_routes[1].call_count == 1
    assert flow.put_routes[2].call_count == 1
    assert flow.put_routes[3].call_count == 2


def test_exact_100_mib_attachment_persists_thirteen_approved_part_digests(
    tmp_path: Path,
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    manifest, attachment, _payload = write_manifest(tmp_path / "max-size")
    with attachment.open("wb") as stream:
        stream.seek(100 * 1024 * 1024 - 1)
        stream.write(b"\0")

    result = runner.invoke(
        app,
        ["submit", "--server", ORIGIN, "--manifest", str(manifest)],
    )

    assert result.exit_code == 3
    state = json.loads(next(state_dir.rglob("*.json")).read_text(encoding="utf-8"))
    digests = state["attachments"][0]["part_sha256s"]
    assert len(digests) == 13
    assert digests[:12] == [hashlib.sha256(b"\0" * PART_SIZE).hexdigest()] * 12
    assert digests[12] == hashlib.sha256(b"\0" * (4 * 1024 * 1024)).hexdigest()
    assert memory_keyring.set_calls == []
    assert len(respx_mock.calls) == 0


def test_tampered_approved_part_digest_exits_2_before_credentials_or_network(
    tmp_path: Path,
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    flow = _install_interrupted_flow(tmp_path, memory_keyring, respx_mock)
    first = runner.invoke(
        app,
        ["submit", "--server", ORIGIN, "--manifest", str(flow.manifest)],
    )
    assert first.exit_code == 6
    entry_path = next(state_dir.rglob("*.json"))
    document = json.loads(entry_path.read_text(encoding="utf-8"))
    document["attachments"][0].setdefault(
        "part_sha256s",
        [hashlib.sha256(flow.attachment.read_bytes()).hexdigest()],
    )
    document["attachments"][0]["part_sha256s"][0] = "0" * 64
    entry_path.write_text(json.dumps(document), encoding="utf-8")
    network_calls = len(respx_mock.calls)
    keyring_reads = len(memory_keyring.get_calls)

    retried = runner.invoke(app, ["retry", "--server", ORIGIN])

    assert retried.exit_code == 2
    assert len(respx_mock.calls) == network_calls
    assert len(memory_keyring.get_calls) == keyring_reads
    assert entry_path.exists()
