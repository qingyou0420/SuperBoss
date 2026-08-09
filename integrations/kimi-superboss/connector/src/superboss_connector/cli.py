"""The intentionally small public command-line surface."""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import Annotated
from uuid import UUID

import typer

from .client import ApiClient, AttachmentResponse, JobResponse, SubmitResponse
from .config import normalize_origin
from .credentials import CredentialStore
from .errors import FILE_CHANGED, INVALID_INPUT, ConnectorError
from .manifest import PreparedManifest, prepare_manifest, verify_attachment
from .outbox import CompletedPart, OutboxEntry, OutboxStore, Phase, initial_entry

app = typer.Typer(add_completion=False, no_args_is_help=True, pretty_exceptions_enable=False)


def _command[**P, R](function: Callable[P, R]) -> Callable[P, R]:
    @wraps(function)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return function(*args, **kwargs)
        except ConnectorError as error:
            typer.echo(error.message, err=True)
            raise typer.Exit(error.exit_code) from None

    return wrapped


def _safe_argument(value: str, *, maximum: int) -> None:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ConnectorError(2, INVALID_INPUT) from error
    if (
        not 1 <= len(value) <= maximum
        or not value.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ConnectorError(2, INVALID_INPUT)


def _print_result(result: SubmitResponse) -> None:
    fields = [str(result.id), result.status]
    if result.result_code is not None:
        fields.append(result.result_code)
    typer.echo(" ".join(fields))


def _prepared_for_retry(entry: OutboxEntry) -> PreparedManifest:
    try:
        manifest = prepare_manifest(Path(entry.manifest_path))
    except ConnectorError as error:
        raise ConnectorError(4, FILE_CHANGED) from error
    if (
        manifest.idempotency_key != entry.idempotency_key
        or manifest.fingerprint != entry.manifest_fingerprint
        or len(manifest.attachments) != len(entry.attachments)
    ):
        raise ConnectorError(4, FILE_CHANGED)
    for prepared, stored in zip(manifest.attachments, entry.attachments, strict=True):
        if (
            prepared.kind != stored.kind
            or str(prepared.path) != stored.path
            or prepared.filename != stored.filename
            or prepared.size_bytes != stored.size_bytes
            or prepared.sha256 != stored.sha256
            or prepared.content_type != stored.content_type
        ):
            raise ConnectorError(4, FILE_CHANGED)
    return manifest


def _bind_created_job(
    entry: OutboxEntry,
    manifest: PreparedManifest,
    response: JobResponse,
) -> None:
    entry.job_id = response.id
    by_kind = {attachment.kind: attachment for attachment in response.attachments}
    if len(by_kind) != len(entry.attachments):
        raise ConnectorError(5, "The server rejected the operation.")
    for stored, prepared in zip(entry.attachments, manifest.attachments, strict=True):
        received = by_kind.get(prepared.kind)
        if received is None:
            raise ConnectorError(5, "The server rejected the operation.")
        stored.attachment_id = received.id
        stored.file_id = received.file_id
        stored.upload_id = received.upload_id
    entry.phase = Phase.UPLOAD


def _read_part(path: Path, part_number: int, part_size: int, total_size: int) -> bytes:
    offset = (part_number - 1) * part_size
    expected_length = min(part_size, total_size - offset)
    try:
        with path.open("rb") as stream:
            stream.seek(offset)
            content = stream.read(part_size)
    except OSError as error:
        raise ConnectorError(4, FILE_CHANGED) from error
    if len(content) != expected_length:
        raise ConnectorError(4, FILE_CHANGED)
    return content


def _resume(
    client: ApiClient,
    access_token: str,
    store: OutboxStore,
    path: Path,
    entry: OutboxEntry,
    manifest: PreparedManifest,
) -> SubmitResponse:
    if entry.phase == Phase.CREATE:
        created = client.create_job(manifest.server, entry.idempotency_key, access_token)
        _bind_created_job(entry, manifest, created)
        store.save(path, entry)

    if entry.job_id is None:
        raise ConnectorError(2, "Local recovery state is invalid.")

    if entry.phase == Phase.UPLOAD:
        for attachment in entry.attachments:
            verify_attachment(
                Path(attachment.path),
                attachment.size_bytes,
                attachment.sha256,
            )
            if (
                attachment.attachment_id is None
                or attachment.file_id is None
                or attachment.upload_id is None
            ):
                raise ConnectorError(2, "Local recovery state is invalid.")
            total_parts = (attachment.size_bytes + attachment.part_size - 1) // attachment.part_size
            completed_numbers = {part.part_number for part in attachment.completed_parts}
            if len(completed_numbers) != len(attachment.completed_parts) or any(
                part_number > total_parts for part_number in completed_numbers
            ):
                raise ConnectorError(2, "Local recovery state is invalid.")
            for part_number in range(1, total_parts + 1):
                if part_number in completed_numbers:
                    continue
                url = client.part_url(
                    entry.job_id,
                    attachment.attachment_id,
                    part_number,
                    access_token,
                )
                verify_attachment(
                    Path(attachment.path),
                    attachment.size_bytes,
                    attachment.sha256,
                )
                content = _read_part(
                    Path(attachment.path),
                    part_number,
                    attachment.part_size,
                    attachment.size_bytes,
                )
                etag = client.put_part(url, content)
                attachment.completed_parts.append(CompletedPart(part_number=part_number, etag=etag))
                attachment.completed_parts.sort(key=lambda part: part.part_number)
                store.save(path, entry)
            if not attachment.completed:
                verify_attachment(
                    Path(attachment.path),
                    attachment.size_bytes,
                    attachment.sha256,
                )
                expected = AttachmentResponse(
                    id=attachment.attachment_id,
                    file_id=attachment.file_id,
                    upload_id=attachment.upload_id,
                    kind=attachment.kind,
                    file_state="UPLOADING",
                )
                client.complete_attachment(
                    entry.job_id,
                    expected,
                    [part.model_dump(mode="json") for part in attachment.completed_parts],
                    access_token,
                )
                attachment.completed = True
                store.save(path, entry)
        entry.phase = Phase.SUBMIT
        store.save(path, entry)

    result = client.submit_job(entry.job_id, access_token)
    store.delete(path)
    return result


def _discard_on_file_change(
    error: ConnectorError,
    store: OutboxStore,
    path: Path,
) -> None:
    if error.exit_code == 4:
        store.delete(path)


@app.command("pair")
@_command
def pair(
    server: Annotated[str, typer.Option("--server")],
    code: Annotated[str, typer.Option("--code")],
    name: Annotated[str, typer.Option("--name")],
) -> None:
    """Pair this workstation and persist only the rotating refresh credential."""
    origin = normalize_origin(server)
    _safe_argument(code, maximum=255)
    _safe_argument(name, maximum=255)
    credentials = CredentialStore(origin)
    with ApiClient(origin) as client:
        client.pair(code, name, credentials)
    typer.echo("Device paired.")


@app.command("submit")
@_command
def submit(
    server: Annotated[str, typer.Option("--server")],
    manifest: Annotated[Path, typer.Option("--manifest")],
) -> None:
    """Validate and submit one confirmed K3 result package."""
    origin = normalize_origin(server)
    prepared = prepare_manifest(manifest)
    store = OutboxStore(origin)
    with store.lock():
        store.ensure_available(prepared.idempotency_key)
        entry = initial_entry(origin, prepared)
        path = store.path_for(prepared.idempotency_key)
        store.save(path, entry)
        credentials = CredentialStore(origin)
        try:
            with ApiClient(origin) as client:
                access_token = client.refresh(credentials)
                result = _resume(client, access_token, store, path, entry, prepared)
        except ConnectorError as error:
            _discard_on_file_change(error, store, path)
            raise
    _print_result(result)


@app.command("status")
@_command
def status(
    server: Annotated[str, typer.Option("--server")],
    job_id: Annotated[UUID, typer.Option("--job-id")],
) -> None:
    """Read one own-device import status."""
    origin = normalize_origin(server)
    credentials = CredentialStore(origin)
    with ApiClient(origin) as client:
        access_token = client.refresh(credentials)
        result = client.status(job_id, access_token)
    fields = [str(result.id), result.status]
    if result.result_code is not None:
        fields.append(result.result_code)
    typer.echo(" ".join(fields))


@app.command("retry")
@_command
def retry(server: Annotated[str, typer.Option("--server")]) -> None:
    """Resume the sole unfinished operation for this origin."""
    origin = normalize_origin(server)
    store = OutboxStore(origin)
    with store.lock():
        path, entry = store.load()
        try:
            prepared = _prepared_for_retry(entry)
            credentials = CredentialStore(origin)
            with ApiClient(origin) as client:
                access_token = client.refresh(credentials)
                result = _resume(client, access_token, store, path, entry, prepared)
        except ConnectorError as error:
            _discard_on_file_change(error, store, path)
            raise
    _print_result(result)


def main() -> None:
    app(prog_name="superboss")
