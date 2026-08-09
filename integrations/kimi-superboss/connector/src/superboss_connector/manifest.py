"""Strict local-envelope validation and server manifest derivation."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import stat
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .config import ATTACHMENT_MAX_BYTES, MANIFEST_MAX_UTF8_BYTES
from .errors import INVALID_INPUT, ConnectorError

_MODEL_CONFIG = ConfigDict(
    extra="forbid",
    hide_input_in_errors=True,
    str_strip_whitespace=True,
)
_CONTENT_TYPE = re.compile(r"[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+")
_DIGEST = re.compile(r"[0-9a-f]{64}")


class AttachmentKind(StrEnum):
    ORIGINAL = "ORIGINAL"
    REVISED = "REVISED"
    K3_RAW = "K3_RAW"


_ATTACHMENT_ORDER = {
    AttachmentKind.ORIGINAL: 0,
    AttachmentKind.REVISED: 1,
    AttachmentKind.K3_RAW: 2,
}


def _normalize_text(value: object, *, document: bool = False) -> object:
    if not isinstance(value, str):
        return value
    normalized = unicodedata.normalize("NFC", value)
    if document:
        normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    return normalized


def _normalize_list(value: object, *, document: bool = False) -> object:
    if not isinstance(value, list):
        return value
    return [_normalize_text(item, document=document) for item in value]


def _safe_text(value: str, *, document: bool = False) -> str:
    if any(
        ord(character) == 127 or (ord(character) < 32 and not (document and character == "\n"))
        for character in value
    ):
        raise ValueError("unsafe control character")
    return value


Text4096 = Annotated[str, Field(min_length=1, max_length=4_096)]


class K3Result(BaseModel):
    model_config = _MODEL_CONFIG

    model_label: Annotated[str, Field(min_length=1, max_length=128)]
    processed_at: datetime
    modification_details: list[Text4096] = Field(max_length=100)
    knowledge_points: list[Text4096] = Field(max_length=100)
    risks: list[Text4096] = Field(max_length=100)
    suggested_title: Annotated[str, Field(min_length=1, max_length=1_024)] | None = None
    suggested_tags: list[Annotated[str, Field(min_length=1, max_length=128)]] = Field(
        default_factory=list,
        max_length=64,
    )

    @field_validator("model_label", "suggested_title", mode="before")
    @classmethod
    def normalize_scalars(cls, value: object) -> object:
        return _normalize_text(value)

    @field_validator("modification_details", "knowledge_points", "risks", mode="before")
    @classmethod
    def normalize_documents(cls, value: object) -> object:
        return _normalize_list(value, document=True)

    @field_validator("suggested_tags", mode="before")
    @classmethod
    def normalize_tags(cls, value: object) -> object:
        return _normalize_list(value)

    @field_validator("model_label", "suggested_title")
    @classmethod
    def validate_scalars(cls, value: str | None) -> str | None:
        return None if value is None else _safe_text(value)

    @field_validator("modification_details", "knowledge_points", "risks")
    @classmethod
    def validate_documents(cls, value: list[str]) -> list[str]:
        return [_safe_text(item, document=True) for item in value]

    @field_validator("suggested_tags")
    @classmethod
    def validate_tags(cls, value: list[str]) -> list[str]:
        for item in value:
            _safe_text(item)
        if len(set(value)) != len(value):
            raise ValueError("duplicate suggested tag")
        return sorted(value)

    @field_validator("processed_at")
    @classmethod
    def validate_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timezone required")
        return value.astimezone(UTC)


class LocalAttachment(BaseModel):
    model_config = _MODEL_CONFIG

    kind: AttachmentKind
    path: Annotated[str, Field(min_length=1, max_length=4_096)]
    content_type: Annotated[str, Field(min_length=1, max_length=255)] | None = None

    @field_validator("path", mode="before")
    @classmethod
    def normalize_path(cls, value: object) -> object:
        return _normalize_text(value)

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return _safe_text(value)

    @field_validator("content_type")
    @classmethod
    def validate_content_type(cls, value: str | None) -> str | None:
        if value is not None and _CONTENT_TYPE.fullmatch(value) is None:
            raise ValueError("invalid MIME type")
        return value


class LocalManifest(BaseModel):
    model_config = _MODEL_CONFIG

    idempotency_key: Annotated[str, Field(min_length=1, max_length=255)]
    project_id: UUID
    local_task_id: Annotated[str, Field(min_length=1, max_length=255)]
    external_document_reference: Annotated[str, Field(min_length=1, max_length=1_024)] | None = None
    base_sha256: str | None = None
    k3_result: K3Result
    attachments: list[LocalAttachment] = Field(min_length=1, max_length=3)

    @field_validator("idempotency_key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        if not value.strip() or any(
            ord(character) < 32 or ord(character) > 126 for character in value
        ):
            raise ValueError("idempotency key must be printable ASCII")
        return value

    @field_validator("local_task_id", "external_document_reference", mode="before")
    @classmethod
    def normalize_identifiers(cls, value: object) -> object:
        return _normalize_text(value)

    @field_validator("local_task_id", "external_document_reference")
    @classmethod
    def validate_identifiers(cls, value: str | None) -> str | None:
        return None if value is None else _safe_text(value)

    @field_validator("base_sha256")
    @classmethod
    def validate_base_digest(cls, value: str | None) -> str | None:
        if value is not None and _DIGEST.fullmatch(value) is None:
            raise ValueError("invalid base digest")
        return value

    @model_validator(mode="after")
    def validate_attachment_kinds(self) -> Self:
        kinds = [attachment.kind for attachment in self.attachments]
        if len(set(kinds)) != len(kinds) or kinds.count(AttachmentKind.K3_RAW) != 1:
            raise ValueError("invalid attachment kinds")
        if self.base_sha256 is not None and AttachmentKind.ORIGINAL not in kinds:
            raise ValueError("base digest requires original")
        return self


class ServerAttachment(BaseModel):
    model_config = _MODEL_CONFIG

    kind: AttachmentKind
    filename: Annotated[str, Field(min_length=1, max_length=1_024)]
    size_bytes: int = Field(ge=1, le=ATTACHMENT_MAX_BYTES)
    sha256: str
    content_type: Annotated[str, Field(min_length=1, max_length=255)]

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, value: str) -> str:
        return _safe_text(value)

    @field_validator("sha256")
    @classmethod
    def validate_digest(cls, value: str) -> str:
        if _DIGEST.fullmatch(value) is None:
            raise ValueError("invalid digest")
        return value

    @field_validator("content_type")
    @classmethod
    def validate_content_type(cls, value: str) -> str:
        if _CONTENT_TYPE.fullmatch(value) is None:
            raise ValueError("invalid MIME type")
        return value


class ServerManifest(BaseModel):
    model_config = _MODEL_CONFIG

    project_id: UUID
    local_task_id: Annotated[str, Field(min_length=1, max_length=255)]
    external_document_reference: Annotated[str, Field(min_length=1, max_length=1_024)] | None = None
    base_sha256: str | None = None
    k3_result: K3Result
    attachments: list[ServerAttachment] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def validate_size_budget(self) -> Self:
        payload = self.model_dump(mode="json")
        compact = _json_bytes(payload, separators=(",", ":"))
        postgres = _json_bytes(payload, separators=(", ", ": "))
        if max(len(compact), len(postgres)) > MANIFEST_MAX_UTF8_BYTES:
            raise ValueError("manifest too large")
        return self


@dataclass(frozen=True)
class PreparedAttachment:
    kind: AttachmentKind
    path: Path
    filename: str
    size_bytes: int
    sha256: str
    content_type: str


@dataclass(frozen=True)
class PreparedManifest:
    path: Path
    idempotency_key: str
    server: ServerManifest
    attachments: tuple[PreparedAttachment, ...]
    fingerprint: str


def _json_bytes(payload: object, *, separators: tuple[str, str]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=separators,
    ).encode("utf-8")


def _hash_regular_file(path: Path) -> tuple[int, str]:
    try:
        before = path.stat()
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_size < 1
            or before.st_size > ATTACHMENT_MAX_BYTES
        ):
            raise ConnectorError(2, INVALID_INPUT)
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        after = path.stat()
    except ConnectorError:
        raise
    except (OSError, ValueError) as error:
        raise ConnectorError(2, INVALID_INPUT) from error
    before_fingerprint = (before.st_size, before.st_mtime_ns, before.st_mode)
    after_fingerprint = (after.st_size, after.st_mtime_ns, after.st_mode)
    if before_fingerprint != after_fingerprint:
        raise ConnectorError(2, INVALID_INPUT)
    return before.st_size, digest.hexdigest()


def prepare_manifest(path: Path) -> PreparedManifest:
    """Validate the entire local envelope and all attachment bytes."""
    try:
        manifest_path = path.resolve(strict=True)
        if not manifest_path.is_file():
            raise OSError("not a file")
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        local = LocalManifest.model_validate(raw)
    except (OSError, UnicodeError, json.JSONDecodeError, ValidationError, ValueError) as error:
        raise ConnectorError(2, INVALID_INPUT) from error

    prepared: list[PreparedAttachment] = []
    resolved_seen: set[Path] = set()
    base = manifest_path.parent
    try:
        for declaration in local.attachments:
            candidate = Path(declaration.path)
            if candidate.is_absolute():
                raise ConnectorError(2, INVALID_INPUT)
            unresolved = base / candidate
            if unresolved.is_symlink():
                raise ConnectorError(2, INVALID_INPUT)
            resolved = unresolved.resolve(strict=True)
            if not resolved.is_relative_to(base) or resolved in resolved_seen:
                raise ConnectorError(2, INVALID_INPUT)
            resolved_seen.add(resolved)
            size_bytes, digest = _hash_regular_file(resolved)
            content_type = declaration.content_type or mimetypes.guess_type(resolved.name)[0]
            content_type = content_type or "application/octet-stream"
            server_attachment = ServerAttachment(
                kind=declaration.kind,
                filename=unicodedata.normalize("NFC", resolved.name),
                size_bytes=size_bytes,
                sha256=digest,
                content_type=content_type,
            )
            prepared.append(
                PreparedAttachment(
                    kind=declaration.kind,
                    path=resolved,
                    filename=server_attachment.filename,
                    size_bytes=size_bytes,
                    sha256=digest,
                    content_type=content_type,
                )
            )
        prepared.sort(key=lambda attachment: _ATTACHMENT_ORDER[attachment.kind])
        server = ServerManifest(
            project_id=local.project_id,
            local_task_id=local.local_task_id,
            external_document_reference=local.external_document_reference,
            base_sha256=local.base_sha256,
            k3_result=local.k3_result,
            attachments=[
                ServerAttachment(
                    kind=item.kind,
                    filename=item.filename,
                    size_bytes=item.size_bytes,
                    sha256=item.sha256,
                    content_type=item.content_type,
                )
                for item in prepared
            ],
        )
    except (OSError, ValidationError, ValueError) as error:
        raise ConnectorError(2, INVALID_INPUT) from error
    canonical = _json_bytes(server.model_dump(mode="json"), separators=(",", ":"))
    return PreparedManifest(
        path=manifest_path,
        idempotency_key=local.idempotency_key,
        server=server,
        attachments=tuple(prepared),
        fingerprint=hashlib.sha256(canonical).hexdigest(),
    )


def verify_attachment(path: Path, expected_size: int, expected_sha256: str) -> None:
    try:
        size, digest = _hash_regular_file(path)
    except ConnectorError as error:
        raise ConnectorError(
            4, "A local attachment changed; submit a new result package."
        ) from error
    if size != expected_size or digest != expected_sha256:
        raise ConnectorError(4, "A local attachment changed; submit a new result package.")


def server_payload(manifest: ServerManifest) -> dict[str, Any]:
    return manifest.model_dump(mode="json")
