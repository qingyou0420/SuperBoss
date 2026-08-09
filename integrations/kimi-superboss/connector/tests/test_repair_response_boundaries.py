from __future__ import annotations

import hashlib
import json
from copy import deepcopy
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
    TIMESTAMP,
    USERNAME,
    MemoryKeyring,
    attachment_payload,
    expected_server_manifest,
    job_payload,
    load_app,
    rewrite_manifest,
    token_payload,
    write_manifest,
)
from typer.testing import CliRunner

RESPONSE_LIMIT = 128 * 1024
STREAM_CHUNK = 4096
PUBLIC_PUT_URL = "https://93.184.216.34/approved-part"


class CountingJsonStream(httpx.SyncByteStream):
    def __init__(self, document: dict[str, Any]) -> None:
        padded = dict(document)
        padded["padding"] = "X" * (RESPONSE_LIMIT + STREAM_CHUNK)
        self.content = json.dumps(padded, separators=(",", ":")).encode("utf-8")
        self.bytes_yielded = 0

    def __iter__(self) -> Any:
        for offset in range(0, len(self.content), STREAM_CHUNK):
            chunk = self.content[offset : offset + STREAM_CHUNK]
            self.bytes_yielded += len(chunk)
            yield chunk


class UnreadableEncodedStream(httpx.SyncByteStream):
    def __init__(self) -> None:
        self.iterations = 0

    def __iter__(self) -> Any:
        self.iterations += 1
        pytest.fail("encoded response body must not be read")
        yield b""  # pragma: no cover - keeps this a byte iterator


class RawJsonStream(httpx.SyncByteStream):
    def __init__(self, document: dict[str, Any]) -> None:
        self.content = json.dumps(document, separators=(",", ":")).encode("utf-8")
        self.bytes_yielded = 0

    def __iter__(self) -> Any:
        self.bytes_yielded += len(self.content)
        yield self.content


def _streaming_response(document: dict[str, Any], *, status_code: int = 200) -> tuple[Any, Any]:
    stream = CountingJsonStream(document)
    response = httpx.Response(
        status_code,
        headers={"Content-Type": "application/json"},
        stream=stream,
    )
    return response, stream


def _assert_stream_was_stopped_at_limit(stream: CountingJsonStream) -> None:
    assert stream.bytes_yielded <= RESPONSE_LIMIT + STREAM_CHUNK
    assert stream.bytes_yielded < len(stream.content)


def _encoded_response(*, status_code: int = 200) -> tuple[httpx.Response, UnreadableEncodedStream]:
    stream = UnreadableEncodedStream()
    return (
        httpx.Response(
            status_code,
            headers={"Content-Type": "application/json", "Content-Encoding": "gzip"},
            stream=stream,
        ),
        stream,
    )


def _assert_api_requests_identity_encoded(router: respx.MockRouter) -> None:
    api_calls = [call for call in router.calls if call.request.url.host == "nightforest.com"]
    assert api_calls
    assert all(call.request.headers.get("Accept-Encoding") == "identity" for call in api_calls)


@dataclass
class OnePartFlow:
    manifest: Path
    job_id: UUID
    attachment_id: UUID
    file_id: UUID
    upload_id: UUID
    create_route: Any
    part_route: Any
    put_route: Any
    complete_route: Any
    submit_route: Any


def test_api_json_uses_identity_request_and_raw_response_iterator(
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    stream = RawJsonStream(token_payload(access="raw-access", refresh="raw-refresh"))
    respx_mock.post(f"{ORIGIN}/api/v1/device-auth/pair").mock(
        return_value=httpx.Response(
            200,
            headers={
                "Content-Type": "application/json",
                "Content-Encoding": "identity",
            },
            stream=stream,
        )
    )

    def decoded_iterator_forbidden(
        _response: httpx.Response,
        chunk_size: int | None = None,
    ) -> Any:
        del chunk_size
        pytest.fail("decoded response iterator must not be used")

    monkeypatch.setattr(httpx.Response, "iter_bytes", decoded_iterator_forbidden)

    result = runner.invoke(
        app,
        ["pair", "--server", ORIGIN, "--code", "raw-code", "--name", "Raw-PC"],
    )

    assert result.exit_code == 0
    assert stream.bytes_yielded == len(stream.content)
    assert memory_keyring.values[(SERVICE, USERNAME)] == "raw-refresh"
    _assert_api_requests_identity_encoded(respx_mock)


@pytest.mark.parametrize("operation", ["pair", "refresh"])
def test_encoded_auth_success_fails_3_without_body_read_or_credential_write(
    operation: str,
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    response, stream = _encoded_response()
    job_id = uuid4()
    if operation == "pair":
        respx_mock.post(f"{ORIGIN}/api/v1/device-auth/pair").mock(return_value=response)
        arguments = [
            "pair",
            "--server",
            ORIGIN,
            "--code",
            "encoded-code",
            "--name",
            "Encoded-PC",
        ]
    else:
        memory_keyring.values[(SERVICE, USERNAME)] = "encoded-old-refresh"
        respx_mock.post(f"{ORIGIN}/api/v1/device-auth/refresh").mock(return_value=response)
        business = respx_mock.get(f"{ORIGIN}/api/v1/device/import-jobs/{job_id}").mock(
            return_value=httpx.Response(500)
        )
        arguments = ["status", "--server", ORIGIN, "--job-id", str(job_id)]

    result = runner.invoke(app, arguments)

    assert result.exit_code == 3
    assert stream.iterations == 0
    assert memory_keyring.set_calls == []
    if operation == "refresh":
        assert business.call_count == 0
    _assert_api_requests_identity_encoded(respx_mock)


@pytest.mark.parametrize("stage", ["create", "part", "complete", "submit"])
def test_encoded_business_success_fails_5_without_advancing_checkpoint(
    stage: str,
    tmp_path: Path,
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    response, stream = _encoded_response(status_code=201 if stage == "create" else 200)
    flow = _install_one_part_flow(
        tmp_path / stage,
        memory_keyring,
        respx_mock,
        overrides={stage: response},
    )

    result = runner.invoke(
        app,
        ["submit", "--server", ORIGIN, "--manifest", str(flow.manifest)],
    )

    assert result.exit_code == 5
    assert stream.iterations == 0
    entry = json.loads(next(state_dir.rglob("*.json")).read_text(encoding="utf-8"))
    expected_phase = (
        "CREATE" if stage == "create" else ("SUBMIT" if stage == "submit" else "UPLOAD")
    )
    assert entry["phase"] == expected_phase
    if stage in {"create", "part"}:
        assert entry["attachments"][0]["completed_parts"] == []
    if stage == "complete":
        assert len(entry["attachments"][0]["completed_parts"]) == 1
        assert entry["attachments"][0]["completed"] is False
    _assert_api_requests_identity_encoded(respx_mock)


def test_encoded_status_success_fails_5_without_body_read_or_outbox(
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    job_id = uuid4()
    memory_keyring.values[(SERVICE, USERNAME)] = "encoded-status-refresh"
    respx_mock.post(f"{ORIGIN}/api/v1/device-auth/refresh").mock(
        return_value=httpx.Response(
            200,
            json=token_payload(
                access="encoded-status-access",
                refresh="encoded-status-rotated",
            ),
        )
    )
    response, stream = _encoded_response()
    respx_mock.get(f"{ORIGIN}/api/v1/device/import-jobs/{job_id}").mock(return_value=response)

    result = runner.invoke(app, ["status", "--server", ORIGIN, "--job-id", str(job_id)])

    assert result.exit_code == 5
    assert stream.iterations == 0
    assert not list(state_dir.rglob("*.json"))
    _assert_api_requests_identity_encoded(respx_mock)


def _install_one_part_flow(
    directory: Path,
    memory_keyring: MemoryKeyring,
    router: respx.MockRouter,
    *,
    overrides: dict[str, httpx.Response] | None = None,
) -> OnePartFlow:
    overrides = overrides or {}
    manifest, attachment, local_payload = write_manifest(directory)
    server_manifest = expected_server_manifest(local_payload, attachment)
    job_id = uuid4()
    attachment_id = uuid4()
    file_id = uuid4()
    upload_id = uuid4()
    memory_keyring.values[(SERVICE, USERNAME)] = "response-refresh"
    router.post(f"{ORIGIN}/api/v1/device-auth/refresh").mock(
        return_value=httpx.Response(
            200,
            json=token_payload(access="response-access", refresh="response-rotated"),
        )
    )
    create_response = httpx.Response(
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
        return_value=overrides.get("create", create_response)
    )
    part_route = router.post(
        f"{ORIGIN}/api/v1/device/import-jobs/{job_id}/attachments/{attachment_id}/parts/1"
    ).mock(
        return_value=overrides.get(
            "part",
            httpx.Response(200, json={"url": PUBLIC_PUT_URL}),
        )
    )
    put_route = router.put(PUBLIC_PUT_URL).mock(
        return_value=httpx.Response(200, headers={"ETag": "response-etag"})
    )
    complete_route = router.post(
        f"{ORIGIN}/api/v1/device/import-jobs/{job_id}/attachments/{attachment_id}/complete"
    ).mock(
        return_value=overrides.get(
            "complete",
            httpx.Response(
                200,
                json=attachment_payload(
                    attachment_id=attachment_id,
                    file_id=file_id,
                    upload_id=upload_id,
                    state="QUARANTINED",
                ),
            ),
        )
    )
    submit_route = router.post(f"{ORIGIN}/api/v1/device/import-jobs/{job_id}/submit").mock(
        return_value=overrides.get(
            "submit",
            httpx.Response(
                200,
                json={
                    "id": str(job_id),
                    "status": "SCANNING",
                    "result_code": None,
                    "submitted_at": "2026-08-10T09:55:00Z",
                    "updated_at": "2026-08-10T09:55:00Z",
                },
            ),
        )
    )
    return OnePartFlow(
        manifest=manifest,
        job_id=job_id,
        attachment_id=attachment_id,
        file_id=file_id,
        upload_id=upload_id,
        create_route=create_route,
        part_route=part_route,
        put_route=put_route,
        complete_route=complete_route,
        submit_route=submit_route,
    )


@pytest.mark.parametrize("operation", ["pair", "refresh"])
def test_auth_response_stream_cap_exits_3_without_credential_write_or_business(
    operation: str,
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    oversized, stream = _streaming_response(
        token_payload(access="bounded-access", refresh="bounded-refresh")
    )
    job_id = uuid4()
    if operation == "pair":
        respx_mock.post(f"{ORIGIN}/api/v1/device-auth/pair").mock(return_value=oversized)
        arguments = [
            "pair",
            "--server",
            ORIGIN,
            "--code",
            "bounded-code",
            "--name",
            "Bounded-PC",
        ]
    else:
        memory_keyring.values[(SERVICE, USERNAME)] = "old-refresh"
        respx_mock.post(f"{ORIGIN}/api/v1/device-auth/refresh").mock(return_value=oversized)
        arguments = ["status", "--server", ORIGIN, "--job-id", str(job_id)]
    business = respx_mock.get(f"{ORIGIN}/api/v1/device/import-jobs/{job_id}").mock(
        return_value=httpx.Response(500)
    )

    result = runner.invoke(app, arguments)

    assert result.exit_code == 3
    assert memory_keyring.set_calls == []
    assert business.call_count == 0
    _assert_stream_was_stopped_at_limit(stream)


@pytest.mark.parametrize("stage", ["create", "part", "complete", "submit"])
def test_business_response_stream_cap_exits_5_without_advancing_checkpoint(
    stage: str,
    tmp_path: Path,
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    placeholder_job = uuid4()
    placeholder_attachment = uuid4()
    documents = {
        "create": job_payload(
            {
                "project_id": str(uuid4()),
                "local_task_id": "oversized-create",
                "external_document_reference": None,
                "base_sha256": None,
                "k3_result": {
                    "model_label": "K3",
                    "processed_at": TIMESTAMP,
                    "modification_details": [],
                    "knowledge_points": [],
                    "risks": [],
                    "suggested_title": None,
                    "suggested_tags": [],
                },
            },
            job_id=placeholder_job,
            attachment_id=placeholder_attachment,
            file_id=uuid4(),
            upload_id=uuid4(),
        ),
        "part": {"url": PUBLIC_PUT_URL},
        "complete": attachment_payload(
            attachment_id=placeholder_attachment,
            file_id=uuid4(),
            upload_id=uuid4(),
            state="QUARANTINED",
        ),
        "submit": {
            "id": str(placeholder_job),
            "status": "SCANNING",
            "result_code": None,
            "submitted_at": "2026-08-10T09:55:00Z",
            "updated_at": "2026-08-10T09:55:00Z",
        },
    }
    oversized, stream = _streaming_response(
        documents[stage],
        status_code=201 if stage == "create" else 200,
    )
    flow = _install_one_part_flow(
        tmp_path / stage,
        memory_keyring,
        respx_mock,
        overrides={stage: oversized},
    )

    result = runner.invoke(
        app,
        ["submit", "--server", ORIGIN, "--manifest", str(flow.manifest)],
    )

    assert result.exit_code == 5
    _assert_stream_was_stopped_at_limit(stream)
    entry = json.loads(next(state_dir.rglob("*.json")).read_text(encoding="utf-8"))
    expected_phase = (
        "CREATE" if stage == "create" else ("SUBMIT" if stage == "submit" else "UPLOAD")
    )
    assert entry["phase"] == expected_phase
    if stage in {"create", "part"}:
        assert entry["attachments"][0]["completed_parts"] == []
    if stage == "complete":
        assert len(entry["attachments"][0]["completed_parts"]) == 1
        assert entry["attachments"][0]["completed"] is False


def test_status_response_stream_cap_exits_5_without_output_or_outbox(
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    job_id = uuid4()
    memory_keyring.values[(SERVICE, USERNAME)] = "status-refresh"
    respx_mock.post(f"{ORIGIN}/api/v1/device-auth/refresh").mock(
        return_value=httpx.Response(
            200,
            json=token_payload(access="status-access", refresh="status-rotated"),
        )
    )
    response = job_payload(
        {
            "project_id": str(uuid4()),
            "local_task_id": "bounded-status",
            "external_document_reference": None,
            "base_sha256": None,
            "k3_result": {
                "model_label": "K3",
                "processed_at": TIMESTAMP,
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
    oversized, stream = _streaming_response(response)
    respx_mock.get(f"{ORIGIN}/api/v1/device/import-jobs/{job_id}").mock(return_value=oversized)

    result = runner.invoke(app, ["status", "--server", ORIGIN, "--job-id", str(job_id)])

    assert result.exit_code == 5
    _assert_stream_was_stopped_at_limit(stream)
    assert str(job_id) not in result.stdout
    assert not list(state_dir.rglob("*.json"))


@pytest.mark.parametrize(
    ("operation", "content_length", "expected_exit"),
    [
        ("pair", "-1", 3),
        ("pair", "not-a-number", 3),
        ("status", "-1", 5),
        ("status", "not-a-number", 5),
    ],
)
def test_invalid_content_length_is_rejected_without_reading_body(
    operation: str,
    content_length: str,
    expected_exit: int,
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    stream = CountingJsonStream(token_payload(access="unused-access", refresh="unused-refresh"))
    response = httpx.Response(
        200,
        headers={"Content-Type": "application/json", "Content-Length": content_length},
        stream=stream,
    )
    job_id = uuid4()
    if operation == "pair":
        respx_mock.post(f"{ORIGIN}/api/v1/device-auth/pair").mock(return_value=response)
        arguments = [
            "pair",
            "--server",
            ORIGIN,
            "--code",
            "length-code",
            "--name",
            "Length-PC",
        ]
    else:
        memory_keyring.values[(SERVICE, USERNAME)] = "length-refresh"
        respx_mock.post(f"{ORIGIN}/api/v1/device-auth/refresh").mock(
            return_value=httpx.Response(
                200,
                json=token_payload(access="length-access", refresh="length-rotated"),
            )
        )
        respx_mock.get(f"{ORIGIN}/api/v1/device/import-jobs/{job_id}").mock(return_value=response)
        arguments = ["status", "--server", ORIGIN, "--job-id", str(job_id)]

    result = runner.invoke(app, arguments)

    assert result.exit_code == expected_exit
    assert stream.bytes_yielded == 0


@pytest.mark.parametrize(("operation", "expected_exit"), [("pair", 3), ("status", 5)])
def test_deep_json_recursion_is_mapped_to_stable_endpoint_exit(
    operation: str,
    expected_exit: int,
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    deeply_nested = b"[" * 10_000 + b"]" * 10_000
    job_id = uuid4()
    if operation == "pair":
        respx_mock.post(f"{ORIGIN}/api/v1/device-auth/pair").mock(
            return_value=httpx.Response(200, content=deeply_nested)
        )
        arguments = [
            "pair",
            "--server",
            ORIGIN,
            "--code",
            "deep-code",
            "--name",
            "Deep-PC",
        ]
    else:
        memory_keyring.values[(SERVICE, USERNAME)] = "deep-refresh"
        respx_mock.post(f"{ORIGIN}/api/v1/device-auth/refresh").mock(
            return_value=httpx.Response(
                200,
                json=token_payload(access="deep-access", refresh="deep-rotated"),
            )
        )
        respx_mock.get(f"{ORIGIN}/api/v1/device/import-jobs/{job_id}").mock(
            return_value=httpx.Response(200, content=deeply_nested)
        )
        arguments = ["status", "--server", ORIGIN, "--job-id", str(job_id)]

    result = runner.invoke(app, arguments)

    assert result.exit_code == expected_exit
    assert memory_keyring.set_calls == (
        [] if operation == "pair" else [(SERVICE, USERNAME, "deep-rotated")]
    )


def test_near_manifest_limit_create_response_is_not_rejected_by_response_cap(
    tmp_path: Path,
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    manifest, attachment, local_payload = write_manifest(tmp_path / "large-valid-create")
    local_payload["k3_result"]["modification_details"] = ["D" * 4096 for _ in range(15)]
    rewrite_manifest(manifest, local_payload)
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
    encoded = json.dumps(response, separators=(",", ":")).encode("utf-8")
    assert 60 * 1024 < len(encoded) < RESPONSE_LIMIT
    memory_keyring.values[(SERVICE, USERNAME)] = "large-create-refresh"
    respx_mock.post(f"{ORIGIN}/api/v1/device-auth/refresh").mock(
        return_value=httpx.Response(
            200,
            json=token_payload(access="large-create-access", refresh="large-create-rotated"),
        )
    )
    respx_mock.post(f"{ORIGIN}/api/v1/device/import-jobs").mock(
        return_value=httpx.Response(201, json=response)
    )
    part = respx_mock.post(
        f"{ORIGIN}/api/v1/device/import-jobs/{job_id}/attachments/{attachment_id}/parts/1"
    ).mock(return_value=httpx.Response(503))

    result = runner.invoke(
        app,
        ["submit", "--server", ORIGIN, "--manifest", str(manifest)],
    )

    assert result.exit_code == 6
    assert part.call_count == 1
    entry = json.loads(next(state_dir.rglob("*.json")).read_text(encoding="utf-8"))
    assert entry["phase"] == "UPLOAD"


def test_near_manifest_limit_status_response_is_not_rejected_by_response_cap(
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    job_id = uuid4()
    memory_keyring.values[(SERVICE, USERNAME)] = "large-status-refresh"
    respx_mock.post(f"{ORIGIN}/api/v1/device-auth/refresh").mock(
        return_value=httpx.Response(
            200,
            json=token_payload(access="large-status-access", refresh="large-status-rotated"),
        )
    )
    response = job_payload(
        {
            "project_id": str(uuid4()),
            "local_task_id": "large-valid-status",
            "external_document_reference": None,
            "base_sha256": None,
            "k3_result": {
                "model_label": "K3",
                "processed_at": TIMESTAMP,
                "modification_details": ["D" * 4096 for _ in range(15)],
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
    response["submitted_at"] = TIMESTAMP
    encoded = json.dumps(response, separators=(",", ":")).encode("utf-8")
    assert 60 * 1024 < len(encoded) < RESPONSE_LIMIT
    respx_mock.get(f"{ORIGIN}/api/v1/device/import-jobs/{job_id}").mock(
        return_value=httpx.Response(200, json=response)
    )

    result = runner.invoke(app, ["status", "--server", ORIGIN, "--job-id", str(job_id)])

    assert result.exit_code == 0
    assert str(job_id) in result.stdout and "SCANNING" in result.stdout


@pytest.mark.parametrize(
    "mutation",
    [
        "access_too_long",
        "refresh_too_long",
        "access_surrogate",
        "refresh_surrogate",
        "access_control",
        "naive_expiry",
        "naive_refresh_expiry",
    ],
)
def test_auth_response_fields_are_bounded_and_timezone_aware(
    mutation: str,
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    response = token_payload(access="bounded-access", refresh="bounded-refresh")
    if mutation == "access_too_long":
        response["access_token"] = "A" * 4097
    elif mutation == "refresh_too_long":
        response["refresh_token"] = "R" * 4097
    elif mutation == "access_surrogate":
        response["access_token"] = "bad\ud800access"
    elif mutation == "refresh_surrogate":
        response["refresh_token"] = "bad\ud800refresh"
    elif mutation == "access_control":
        response["access_token"] = "bad\u0000access"
    elif mutation == "naive_expiry":
        response["expires_at"] = "2026-08-10T11:54:00"
    else:
        response["refresh_expires_at"] = "2026-08-24T09:54:00"
    respx_mock.post(f"{ORIGIN}/api/v1/device-auth/pair").mock(
        return_value=httpx.Response(
            200,
            content=json.dumps(response, ensure_ascii=True).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
    )

    result = runner.invoke(
        app,
        ["pair", "--server", ORIGIN, "--code", "bounded", "--name", "Bounded-PC"],
    )

    assert result.exit_code == 3
    assert memory_keyring.set_calls == []
    combined = f"{result.stdout}\n{result.stderr}"
    assert "A" * 128 not in combined and "R" * 128 not in combined


@pytest.mark.parametrize("mutation", ["status", "file_state", "created_at", "updated_at"])
def test_create_response_only_accepts_initial_uploading_state_and_aware_times(
    mutation: str,
    tmp_path: Path,
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    manifest, attachment, local_payload = write_manifest(tmp_path / mutation)
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
    if mutation == "status":
        response["status"] = "SCANNING"
    elif mutation == "file_state":
        response["attachments"][0]["file_state"] = "QUARANTINED"
    elif mutation == "created_at":
        response["created_at"] = "2026-08-10T09:54:00"
    else:
        response["updated_at"] = "2026-08-10T09:54:00"
    memory_keyring.values[(SERVICE, USERNAME)] = "create-refresh"
    respx_mock.post(f"{ORIGIN}/api/v1/device-auth/refresh").mock(
        return_value=httpx.Response(
            200,
            json=token_payload(access="create-access", refresh="create-rotated"),
        )
    )
    respx_mock.post(f"{ORIGIN}/api/v1/device/import-jobs").mock(
        return_value=httpx.Response(201, json=response)
    )
    respx_mock.post(
        f"{ORIGIN}/api/v1/device/import-jobs/{job_id}/attachments/{attachment_id}/parts/1"
    ).mock(return_value=httpx.Response(503))

    result = runner.invoke(
        app,
        ["submit", "--server", ORIGIN, "--manifest", str(manifest)],
    )

    assert result.exit_code == 5
    entry = json.loads(next(state_dir.rglob("*.json")).read_text(encoding="utf-8"))
    assert entry["phase"] == "CREATE"
    assert entry["job_id"] is None


@pytest.mark.parametrize("identifier", ["id", "file_id", "upload_id"])
def test_create_response_requires_each_remote_identifier_set_unique(
    identifier: str,
    tmp_path: Path,
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    manifest, k3_file, local_payload = write_manifest(tmp_path / identifier)
    revised = manifest.parent / "revised.docx"
    revised.write_bytes(b"revised")
    local_payload["attachments"].append(
        {
            "kind": "REVISED",
            "path": revised.name,
            "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }
    )
    rewrite_manifest(manifest, local_payload)
    server_manifest = deepcopy(
        {
            key: local_payload[key]
            for key in (
                "project_id",
                "local_task_id",
                "external_document_reference",
                "base_sha256",
                "k3_result",
            )
        }
    )
    server_manifest["k3_result"]["processed_at"] = TIMESTAMP
    server_manifest["attachments"] = [
        {
            "kind": "REVISED",
            "filename": revised.name,
            "size_bytes": revised.stat().st_size,
            "sha256": hashlib.sha256(revised.read_bytes()).hexdigest(),
            "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        },
        {
            "kind": "K3_RAW",
            "filename": k3_file.name,
            "size_bytes": k3_file.stat().st_size,
            "sha256": hashlib.sha256(k3_file.read_bytes()).hexdigest(),
            "content_type": "application/json",
        },
    ]
    job_id = uuid4()
    first_ids = {"id": uuid4(), "file_id": uuid4(), "upload_id": uuid4()}
    second_ids = {"id": uuid4(), "file_id": uuid4(), "upload_id": uuid4()}
    second_ids[identifier] = first_ids[identifier]
    response = {
        "id": str(job_id),
        "project_id": server_manifest["project_id"],
        "local_task_id": server_manifest["local_task_id"],
        "external_document_reference": server_manifest["external_document_reference"],
        "base_sha256": None,
        "status": "UPLOADING",
        "result_code": None,
        "k3_result": server_manifest["k3_result"],
        "submitted_at": None,
        "created_at": TIMESTAMP,
        "updated_at": TIMESTAMP,
        "attachments": [
            {
                **{key: str(value) for key, value in first_ids.items()},
                "kind": "REVISED",
                "file_state": "UPLOADING",
            },
            {
                **{key: str(value) for key, value in second_ids.items()},
                "kind": "K3_RAW",
                "file_state": "UPLOADING",
            },
        ],
    }
    memory_keyring.values[(SERVICE, USERNAME)] = "unique-refresh"
    respx_mock.post(f"{ORIGIN}/api/v1/device-auth/refresh").mock(
        return_value=httpx.Response(
            200,
            json=token_payload(access="unique-access", refresh="unique-rotated"),
        )
    )
    respx_mock.post(f"{ORIGIN}/api/v1/device/import-jobs").mock(
        return_value=httpx.Response(201, json=response)
    )
    for remote_attachment in response["attachments"]:
        respx_mock.post(
            f"{ORIGIN}/api/v1/device/import-jobs/{job_id}/attachments/"
            f"{remote_attachment['id']}/parts/1"
        ).mock(return_value=httpx.Response(503))

    result = runner.invoke(
        app,
        ["submit", "--server", ORIGIN, "--manifest", str(manifest)],
    )

    assert result.exit_code == 5
    entry = json.loads(next(state_dir.rglob("*.json")).read_text(encoding="utf-8"))
    assert entry["phase"] == "CREATE"


def test_complete_response_cannot_remain_uploading_and_checkpoint_stays_incomplete(
    tmp_path: Path,
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    flow = _install_one_part_flow(
        tmp_path / "complete-uploading",
        memory_keyring,
        respx_mock,
    )
    flow.complete_route.mock(
        return_value=httpx.Response(
            200,
            json=attachment_payload(
                attachment_id=flow.attachment_id,
                file_id=flow.file_id,
                upload_id=flow.upload_id,
                state="UPLOADING",
            ),
        )
    )

    result = runner.invoke(
        app,
        ["submit", "--server", ORIGIN, "--manifest", str(flow.manifest)],
    )

    assert result.exit_code == 5
    entry = json.loads(next(state_dir.rglob("*.json")).read_text(encoding="utf-8"))
    assert entry["phase"] == "UPLOAD"
    assert entry["attachments"][0]["completed"] is False


@pytest.mark.parametrize("mutation", ["uploading", "naive_time"])
def test_submit_response_requires_terminal_state_and_aware_timestamps(
    mutation: str,
    tmp_path: Path,
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    flow = _install_one_part_flow(
        tmp_path / f"submit-{mutation}",
        memory_keyring,
        respx_mock,
    )
    response = {
        "id": str(flow.job_id),
        "status": "UPLOADING" if mutation == "uploading" else "SCANNING",
        "result_code": None,
        "submitted_at": "2026-08-10T09:55:00Z",
        "updated_at": (
            "2026-08-10T09:55:00Z" if mutation == "uploading" else "2026-08-10T09:55:00"
        ),
    }
    flow.submit_route.mock(return_value=httpx.Response(200, json=response))

    result = runner.invoke(
        app,
        ["submit", "--server", ORIGIN, "--manifest", str(flow.manifest)],
    )

    assert result.exit_code == 5
    entry = json.loads(next(state_dir.rglob("*.json")).read_text(encoding="utf-8"))
    assert entry["phase"] == "SUBMIT"


@pytest.mark.parametrize(
    "mutation",
    [
        "uploading_with_submitted_at",
        "scanning_without_submitted_at",
        "rejected_without_code",
        "received_with_code",
        "naive_updated_at",
        "updated_before_created",
        "submitted_after_updated",
        "naive_processed_at",
    ],
)
def test_status_response_enforces_result_and_timestamp_semantics(
    mutation: str,
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    job_id = uuid4()
    memory_keyring.values[(SERVICE, USERNAME)] = "semantic-refresh"
    respx_mock.post(f"{ORIGIN}/api/v1/device-auth/refresh").mock(
        return_value=httpx.Response(
            200,
            json=token_payload(access="semantic-access", refresh="semantic-rotated"),
        )
    )
    response = job_payload(
        {
            "project_id": str(uuid4()),
            "local_task_id": "semantic-status",
            "external_document_reference": None,
            "base_sha256": None,
            "k3_result": {
                "model_label": "K3",
                "processed_at": TIMESTAMP,
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
    )
    if mutation == "uploading_with_submitted_at":
        response["submitted_at"] = TIMESTAMP
    elif mutation == "scanning_without_submitted_at":
        response["status"] = "SCANNING"
        response["submitted_at"] = None
        response["attachments"][0]["file_state"] = "QUARANTINED"
    elif mutation == "rejected_without_code":
        response["status"] = "REJECTED"
        response["submitted_at"] = TIMESTAMP
        response["attachments"][0]["file_state"] = "QUARANTINED"
    elif mutation == "received_with_code":
        response["status"] = "RECEIVED"
        response["result_code"] = "UNEXPECTED_CODE"
        response["submitted_at"] = TIMESTAMP
        response["attachments"][0]["file_state"] = "CLEAN"
    elif mutation == "naive_updated_at":
        response["updated_at"] = "2026-08-10T09:54:00"
    elif mutation == "submitted_after_updated":
        response["status"] = "SCANNING"
        response["submitted_at"] = "2026-08-10T10:00:00Z"
        response["attachments"][0]["file_state"] = "QUARANTINED"
    elif mutation == "naive_processed_at":
        response["k3_result"]["processed_at"] = "2026-08-10T09:54:00"
    else:
        response["created_at"] = "2026-08-10T10:00:00Z"
        response["updated_at"] = "2026-08-10T09:54:00Z"
    respx_mock.get(f"{ORIGIN}/api/v1/device/import-jobs/{job_id}").mock(
        return_value=httpx.Response(200, json=response)
    )

    result = runner.invoke(app, ["status", "--server", ORIGIN, "--job-id", str(job_id)])

    assert result.exit_code == 5
    assert str(job_id) not in result.stdout


def test_presigned_put_does_not_buffer_or_read_provider_response_body(
    tmp_path: Path,
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    flow = _install_one_part_flow(
        tmp_path / "provider-body",
        memory_keyring,
        respx_mock,
    )
    stream = CountingJsonStream({"provider": "body-must-not-be-read"})
    flow.put_route.mock(
        return_value=httpx.Response(
            200,
            headers={"ETag": "provider-etag"},
            stream=stream,
        )
    )

    result = runner.invoke(
        app,
        ["submit", "--server", ORIGIN, "--manifest", str(flow.manifest)],
    )

    assert result.exit_code == 0
    assert flow.put_route.call_count == 1
    assert stream.bytes_yielded == 0
