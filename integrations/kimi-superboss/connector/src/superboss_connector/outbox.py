"""Atomic, locked, non-secret recovery state."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, Self
from uuid import UUID

import portalocker
from platformdirs import user_state_dir
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from .config import ETAG_MAX_CHARS, LOCK_TIMEOUT_SECONDS
from .errors import OUTBOX_BUSY, OUTBOX_CONFLICT, OUTBOX_INVALID, ConnectorError
from .manifest import AttachmentKind, PreparedManifest


class Phase(StrEnum):
    CREATE = "CREATE"
    UPLOAD = "UPLOAD"
    SUBMIT = "SUBMIT"


class CompletedPart(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    part_number: int = Field(ge=1, le=10_000)
    etag: str = Field(min_length=1, max_length=ETAG_MAX_CHARS)

    @field_validator("etag")
    @classmethod
    def safe_etag(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("unsafe etag")
        return value


class AttachmentState(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    kind: AttachmentKind
    path: Annotated[str, Field(min_length=1, max_length=32_767)]
    filename: Annotated[str, Field(min_length=1, max_length=1_024)]
    size_bytes: int = Field(ge=1, le=100 * 1024 * 1024)
    sha256: str
    content_type: Annotated[str, Field(min_length=1, max_length=255)]
    part_size: Literal[8_388_608] = 8_388_608
    attachment_id: UUID | None = None
    file_id: UUID | None = None
    upload_id: UUID | None = None
    completed_parts: list[CompletedPart] = Field(default_factory=list)
    completed: bool = False

    @field_validator("path", "filename", "content_type")
    @classmethod
    def safe_text(cls, value: str) -> str:
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("unsafe state text")
        return value

    @field_validator("sha256")
    @classmethod
    def safe_digest(cls, value: str) -> str:
        if re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise ValueError("invalid digest")
        return value

    @model_validator(mode="after")
    def consistent_progress(self) -> Self:
        identifiers = (self.attachment_id, self.file_id, self.upload_id)
        if any(value is None for value in identifiers) != all(
            value is None for value in identifiers
        ):
            raise ValueError("partial attachment identifiers")
        part_numbers = [part.part_number for part in self.completed_parts]
        total_parts = (self.size_bytes + self.part_size - 1) // self.part_size
        if (
            len(part_numbers) != len(set(part_numbers))
            or part_numbers != sorted(part_numbers)
            or any(number > total_parts for number in part_numbers)
            or (self.completed and len(part_numbers) != total_parts)
        ):
            raise ValueError("invalid part progress")
        return self


class OutboxEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    normalized_origin: Annotated[str, Field(min_length=1, max_length=4_096)]
    idempotency_key: Annotated[str, Field(min_length=1, max_length=255)]
    manifest_path: Annotated[str, Field(min_length=1, max_length=32_767)]
    manifest_fingerprint: str
    job_id: UUID | None = None
    phase: Phase = Phase.CREATE
    attachments: list[AttachmentState] = Field(min_length=1, max_length=3)
    updated_at: datetime

    @field_validator("normalized_origin", "manifest_path")
    @classmethod
    def safe_text(cls, value: str) -> str:
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("unsafe state text")
        return value

    @field_validator("idempotency_key")
    @classmethod
    def safe_key(cls, value: str) -> str:
        if not value.strip() or any(
            ord(character) < 32 or ord(character) > 126 for character in value
        ):
            raise ValueError("invalid idempotency key")
        return value

    @field_validator("manifest_fingerprint")
    @classmethod
    def safe_fingerprint(cls, value: str) -> str:
        if re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise ValueError("invalid manifest fingerprint")
        return value

    @field_validator("updated_at")
    @classmethod
    def aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timezone required")
        return value

    @model_validator(mode="after")
    def consistent_phase(self) -> Self:
        kinds = [attachment.kind for attachment in self.attachments]
        if len(kinds) != len(set(kinds)):
            raise ValueError("duplicate attachment kind")
        if self.phase == Phase.CREATE:
            if self.job_id is not None or any(
                attachment.attachment_id is not None for attachment in self.attachments
            ):
                raise ValueError("create phase has remote identifiers")
            if any(
                attachment.completed_parts or attachment.completed
                for attachment in self.attachments
            ):
                raise ValueError("create phase has upload progress")
        elif self.job_id is None or any(
            attachment.attachment_id is None for attachment in self.attachments
        ):
            raise ValueError("remote phase lacks identifiers")
        if self.phase == Phase.SUBMIT and not all(
            attachment.completed for attachment in self.attachments
        ):
            raise ValueError("submit phase has incomplete attachment")
        return self


def initial_entry(origin: str, manifest: PreparedManifest) -> OutboxEntry:
    return OutboxEntry(
        normalized_origin=origin,
        idempotency_key=manifest.idempotency_key,
        manifest_path=str(manifest.path),
        manifest_fingerprint=manifest.fingerprint,
        attachments=[
            AttachmentState(
                kind=attachment.kind,
                path=str(attachment.path),
                filename=attachment.filename,
                size_bytes=attachment.size_bytes,
                sha256=attachment.sha256,
                content_type=attachment.content_type,
            )
            for attachment in manifest.attachments
        ],
        updated_at=datetime.now(UTC),
    )


class OutboxStore:
    """One bounded, resumable operation per trusted server origin."""

    def __init__(self, origin: str) -> None:
        self.origin = origin
        self.root = Path(user_state_dir("SuperBossKimiConnector"))
        origin_hash = hashlib.sha256(origin.encode("utf-8")).hexdigest()
        self.origin_dir = self.root / origin_hash
        self.lock_path = self.origin_dir / "operation.lock"

    @contextmanager
    def lock(self) -> Iterator[None]:
        try:
            self.origin_dir.mkdir(parents=True, exist_ok=True)
            flags = portalocker.LockFlags.EXCLUSIVE | portalocker.LockFlags.NON_BLOCKING
            with portalocker.Lock(
                self.lock_path,
                mode="a+b",
                timeout=LOCK_TIMEOUT_SECONDS,
                flags=flags,
            ):
                yield
        except ConnectorError:
            raise
        except (OSError, portalocker.exceptions.LockException) as error:
            raise ConnectorError(2, OUTBOX_BUSY) from error

    def _all_documents(self) -> list[tuple[Path, OutboxEntry]]:
        if not self.root.exists():
            return []
        documents: list[tuple[Path, OutboxEntry]] = []
        for path in sorted(self.root.rglob("*.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                entry = OutboxEntry.model_validate(raw)
            except (OSError, UnicodeError, json.JSONDecodeError, ValidationError) as error:
                raise ConnectorError(2, OUTBOX_INVALID) from error
            documents.append((path, entry))
        return documents

    def load(self) -> tuple[Path, OutboxEntry]:
        matches = [
            item for item in self._all_documents() if item[1].normalized_origin == self.origin
        ]
        if len(matches) != 1:
            raise ConnectorError(2, OUTBOX_INVALID)
        if matches[0][0].resolve() != self.path_for(matches[0][1].idempotency_key).resolve():
            raise ConnectorError(2, OUTBOX_INVALID)
        return matches[0]

    def ensure_available(self, idempotency_key: str) -> None:
        matches = [
            item for item in self._all_documents() if item[1].normalized_origin == self.origin
        ]
        if not matches:
            return
        if len(matches) != 1 or matches[0][1].idempotency_key != idempotency_key:
            raise ConnectorError(2, OUTBOX_CONFLICT)
        raise ConnectorError(2, OUTBOX_CONFLICT)

    def path_for(self, idempotency_key: str) -> Path:
        key_hash = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
        return self.origin_dir / f"{key_hash}.json"

    def save(self, path: Path, entry: OutboxEntry) -> None:
        entry.updated_at = datetime.now(UTC)
        try:
            validated = OutboxEntry.model_validate(entry.model_dump(mode="json"))
            serialized = json.dumps(
                validated.model_dump(mode="json"),
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        except (ValidationError, ValueError) as error:
            raise ConnectorError(2, OUTBOX_INVALID) from error
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with temporary.open("wb") as stream:
                stream.write(serialized)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            self._fsync_directory(path.parent)
        except OSError as error:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise ConnectorError(2, OUTBOX_INVALID) from error

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        if os.name == "nt":
            return
        try:
            descriptor = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError:
            return

    def delete(self, path: Path) -> None:
        try:
            path.unlink()
            self._fsync_directory(path.parent)
        except OSError as error:
            raise ConnectorError(2, OUTBOX_INVALID) from error
