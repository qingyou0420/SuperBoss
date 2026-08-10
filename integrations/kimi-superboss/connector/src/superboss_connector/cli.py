"""The intentionally small public command-line surface."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import Annotated
from uuid import UUID

import typer

from .client import ApiClient, AttachmentResponse, JobResponse, SubmitResponse
from .config import normalize_origin
from .credentials import CredentialStore
from .errors import (
    FILE_CHANGED,
    INVALID_INPUT,
    OUTBOX_CONFLICT,
    OUTBOX_INVALID,
    ConnectorError,
)
from .manifest import PreparedManifest, prepare_manifest, verify_attachment
from .outbox import (
    CompletedPart,
    EvidenceState,
    OutboxEntry,
    OutboxStore,
    Phase,
    initial_entry,
)

app = typer.Typer(add_completion=False, no_args_is_help=True, pretty_exceptions_enable=False)


def _safe_echo(message: object, *, err: bool = False) -> None:
    try:
        typer.echo(message, err=err)
    except (OSError, UnicodeError) as error:
        raise ConnectorError(2, OUTBOX_INVALID) from error


def _command[**P, R](function: Callable[P, R]) -> Callable[P, R]:
    @wraps(function)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return function(*args, **kwargs)
        except ConnectorError as error:
            try:
                _safe_echo(error.message, err=True)
            except ConnectorError:
                pass
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


def _print_result(result: SubmitResponse | EvidenceState) -> None:
    identifier = result.id if isinstance(result, SubmitResponse) else result.job_id
    fields = [str(identifier), result.status]
    if result.result_code is not None:
        fields.append(result.result_code)
    _safe_echo(" ".join(fields))


def _print_result_and_delete(
    result: SubmitResponse | EvidenceState,
    store: OutboxStore,
    path: Path,
) -> None:
    _print_result(result)
    store.delete(path)


def _report_replacement() -> None:
    _safe_echo("Device paired. Old operation abandoned; create a new manifest and UUID.")


def _refresh_fingerprint(value: str | None) -> str | None:
    return hashlib.sha256(value.encode("utf-8")).hexdigest() if value is not None else None


def _credential_was_replaced(
    old_credential_state: str,
    old_refresh_sha256: str | None,
    current_refresh: str | None,
) -> bool:
    current_fingerprint = _refresh_fingerprint(current_refresh)
    return (old_credential_state == "MISSING" and current_refresh is not None) or (
        old_credential_state == "PRESENT"
        and current_refresh is not None
        and current_fingerprint != old_refresh_sha256
    )


def _recover_replacement(
    store: OutboxStore,
    credentials: CredentialStore,
    *,
    retain_completed_marker: bool = False,
) -> bool:
    marker = store.load_replacement_marker()
    if marker is None:
        return False
    old_outbox_path = store.marker_outbox_path(marker)
    current_refresh = credentials.load_refresh_optional()
    replacement_is_durable = _credential_was_replaced(
        marker.old_credential_state,
        marker.old_refresh_sha256,
        current_refresh,
    )
    if replacement_is_durable and old_outbox_path.exists():
        store.delete(old_outbox_path)
    if replacement_is_durable and retain_completed_marker:
        return True
    store.delete_replacement_marker()
    return replacement_is_durable


def _recover_initial_pair(store: OutboxStore, credentials: CredentialStore) -> bool:
    marker = store.load_pair_completion_marker()
    if marker is None:
        return False
    current_refresh = credentials.load_refresh_optional()
    if _credential_was_replaced(
        marker.old_credential_state,
        marker.old_refresh_sha256,
        current_refresh,
    ):
        return True
    store.delete_pair_completion_marker()
    return False


def _reject_pending_initial_pair(store: OutboxStore) -> None:
    if store.load_pair_completion_marker() is not None:
        raise ConnectorError(2, OUTBOX_CONFLICT)


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
        if prepared.sha256 == stored.sha256 and list(prepared.part_sha256s) != stored.part_sha256s:
            raise ConnectorError(2, OUTBOX_INVALID)
        if (
            prepared.kind != stored.kind
            or str(prepared.path) != stored.path
            or prepared.filename != stored.filename
            or prepared.size_bytes != stored.size_bytes
            or prepared.sha256 != stored.sha256
            or list(prepared.part_sha256s) != stored.part_sha256s
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


def _read_part(
    path: Path,
    part_number: int,
    part_size: int,
    total_size: int,
    expected_sha256: str,
) -> bytes:
    offset = (part_number - 1) * part_size
    expected_length = min(part_size, total_size - offset)
    try:
        with path.open("rb") as stream:
            stream.seek(offset)
            content = stream.read(part_size)
    except OSError as error:
        raise ConnectorError(4, FILE_CHANGED) from error
    if len(content) != expected_length or hashlib.sha256(content).hexdigest() != expected_sha256:
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
                    attachment.part_sha256s[part_number - 1],
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
    entry.phase = Phase.EVIDENCE
    entry.evidence = EvidenceState(
        job_id=result.id,
        status=result.status,
        result_code=result.result_code,
        submitted_at=result.submitted_at,
        updated_at=result.updated_at,
    )
    store.save(path, entry)
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
    store = OutboxStore(origin)
    credentials = CredentialStore(origin)
    with store.lock():
        if _recover_replacement(
            store,
            credentials,
            retain_completed_marker=True,
        ):
            _report_replacement()
            store.delete_replacement_marker()
            return
        if _recover_initial_pair(store, credentials):
            _safe_echo("Device paired.")
            store.delete_pair_completion_marker()
            return
        existing = store.load_optional()
        if existing is not None and existing[1].phase == Phase.EVIDENCE:
            raise ConnectorError(2, OUTBOX_CONFLICT)
        if existing is not None:
            old_refresh = credentials.load_refresh_optional()
            store.save_replacement_marker(
                old_refresh_sha256=(
                    hashlib.sha256(old_refresh.encode("utf-8")).hexdigest()
                    if old_refresh is not None
                    else None
                ),
                old_outbox_path=existing[0],
            )
        else:
            old_refresh = credentials.load_refresh_optional()
            store.save_pair_completion_marker(
                old_refresh_sha256=_refresh_fingerprint(old_refresh),
            )
        with ApiClient(origin) as client:
            client.pair(code, name, credentials)
        if existing is not None:
            store.delete(existing[0])
            _report_replacement()
            store.delete_replacement_marker()
        else:
            _safe_echo("Device paired.")
            store.delete_pair_completion_marker()


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
        _reject_pending_initial_pair(store)
        credentials = CredentialStore(origin)
        _recover_replacement(store, credentials)
        store.ensure_available(prepared.idempotency_key)
        entry = initial_entry(origin, prepared)
        path = store.path_for(prepared.idempotency_key)
        store.save(path, entry)
        try:
            with ApiClient(origin) as client:
                access_token = client.refresh(credentials)
                result = _resume(client, access_token, store, path, entry, prepared)
        except ConnectorError as error:
            _discard_on_file_change(error, store, path)
            raise
        _print_result_and_delete(result, store, path)


@app.command("status")
@_command
def status(
    server: Annotated[str, typer.Option("--server")],
    job_id: Annotated[UUID, typer.Option("--job-id")],
) -> None:
    """Read one own-device import status."""
    origin = normalize_origin(server)
    store = OutboxStore(origin)
    with store.lock():
        _reject_pending_initial_pair(store)
        credentials = CredentialStore(origin)
        _recover_replacement(store, credentials)
        with ApiClient(origin) as client:
            access_token = client.refresh(credentials)
            result = client.status(job_id, access_token)
        fields = [str(result.id), result.status]
        if result.result_code is not None:
            fields.append(result.result_code)
        _safe_echo(" ".join(fields))


@app.command("retry")
@_command
def retry(server: Annotated[str, typer.Option("--server")]) -> None:
    """Resume the sole unfinished operation for this origin."""
    origin = normalize_origin(server)
    store = OutboxStore(origin)
    with store.lock():
        _reject_pending_initial_pair(store)
        credentials = CredentialStore(origin)
        _recover_replacement(store, credentials)
        path, entry = store.load()
        if entry.phase == Phase.EVIDENCE:
            if entry.evidence is None:
                raise ConnectorError(2, OUTBOX_INVALID)
            _print_result_and_delete(entry.evidence, store, path)
            return
        try:
            prepared = _prepared_for_retry(entry)
            with ApiClient(origin) as client:
                access_token = client.refresh(credentials)
                result = _resume(client, access_token, store, path, entry, prepared)
        except ConnectorError as error:
            _discard_on_file_change(error, store, path)
            raise
        _print_result_and_delete(result, store, path)


def main() -> None:
    app(prog_name="superboss")
