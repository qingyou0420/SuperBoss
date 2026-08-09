"""Strict, normalized request contracts for minimal K3 result packages."""

import json
import re
import unicodedata
from datetime import UTC, datetime
from typing import Annotated, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from superboss.modules.files.models import FileState
from superboss.modules.imports.models import AttachmentKind, ImportStatus

MANIFEST_MAX_UTF8_BYTES = 65_536
MODEL_LABEL_MAX_CHARS = 128
LOCAL_TASK_ID_MAX_CHARS = 255
EXTERNAL_REFERENCE_MAX_CHARS = 1_024
K3_TEXT_MAX_CHARS = 4_096
SUGGESTED_TITLE_MAX_CHARS = 1_024
SUGGESTED_TAG_MAX_CHARS = 128
K3_LIST_MAX_ITEMS = 100
SUGGESTED_TAG_MAX_ITEMS = 64
ATTACHMENT_FILENAME_MAX_CHARS = 1_024
ATTACHMENT_CONTENT_TYPE_MAX_CHARS = 255
ATTACHMENT_MAX_BYTES = 100 * 1024 * 1024

ModelLabel = Annotated[str, Field(min_length=1, max_length=MODEL_LABEL_MAX_CHARS)]
LocalTaskId = Annotated[str, Field(min_length=1, max_length=LOCAL_TASK_ID_MAX_CHARS)]
ExternalReference = Annotated[
    str, Field(min_length=1, max_length=EXTERNAL_REFERENCE_MAX_CHARS)
]
K3Text = Annotated[str, Field(min_length=1, max_length=K3_TEXT_MAX_CHARS)]
SuggestedTitle = Annotated[str, Field(min_length=1, max_length=SUGGESTED_TITLE_MAX_CHARS)]
SuggestedTag = Annotated[str, Field(min_length=1, max_length=SUGGESTED_TAG_MAX_CHARS)]
AttachmentFilename = Annotated[
    str, Field(min_length=1, max_length=ATTACHMENT_FILENAME_MAX_CHARS)
]
ContentType = Annotated[
    str, Field(min_length=1, max_length=ATTACHMENT_CONTENT_TYPE_MAX_CHARS)
]
ResultCode = Annotated[str, Field(min_length=1, max_length=64)]
PartUrl = Annotated[str, Field(min_length=1, max_length=4_096)]

_MODEL_CONFIG = ConfigDict(
    extra="forbid",
    hide_input_in_errors=True,
    str_strip_whitespace=True,
)
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


def _normalize_text_list(value: object, *, document: bool = False) -> object:
    if not isinstance(value, list):
        return value
    return [_normalize_text(item, document=document) for item in value]


def _reject_controls(value: str, *, document: bool = False) -> str:
    if any(
        ord(character) == 127
        or (ord(character) < 32 and not (document and character == "\n"))
        for character in value
    ):
        raise ValueError("must contain no unsafe control characters")
    return value


class AttachmentDeclaration(BaseModel):
    model_config = _MODEL_CONFIG

    kind: AttachmentKind
    filename: AttachmentFilename
    size_bytes: int = Field(ge=1, le=ATTACHMENT_MAX_BYTES)
    sha256: str
    content_type: ContentType

    @field_validator("filename", mode="before")
    @classmethod
    def normalize_filename(cls, value: object) -> object:
        return _normalize_text(value)

    @field_validator("filename")
    @classmethod
    def safe_filename(cls, value: str) -> str:
        return _reject_controls(value)

    @field_validator("sha256")
    @classmethod
    def digest(cls, value: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("sha256 must be 64 lowercase hexadecimal characters")
        return value

    @field_validator("content_type")
    @classmethod
    def safe_content_type(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+", value):
            raise ValueError("content_type must be a MIME type")
        return value


class K3Result(BaseModel):
    model_config = _MODEL_CONFIG

    model_label: ModelLabel
    processed_at: datetime
    modification_details: list[K3Text] = Field(max_length=K3_LIST_MAX_ITEMS)
    knowledge_points: list[K3Text] = Field(max_length=K3_LIST_MAX_ITEMS)
    risks: list[K3Text] = Field(max_length=K3_LIST_MAX_ITEMS)
    suggested_title: SuggestedTitle | None = None
    suggested_tags: list[SuggestedTag] = Field(
        default_factory=list,
        max_length=SUGGESTED_TAG_MAX_ITEMS,
    )

    @field_validator("model_label", "suggested_title", mode="before")
    @classmethod
    def normalize_scalar_text(cls, value: object) -> object:
        return _normalize_text(value)

    @field_validator("modification_details", "knowledge_points", "risks", mode="before")
    @classmethod
    def normalize_document_lists(cls, value: object) -> object:
        return _normalize_text_list(value, document=True)

    @field_validator("suggested_tags", mode="before")
    @classmethod
    def normalize_tags(cls, value: object) -> object:
        return _normalize_text_list(value)

    @field_validator("model_label", "suggested_title")
    @classmethod
    def safe_scalar_text(cls, value: str | None) -> str | None:
        return None if value is None else _reject_controls(value)

    @field_validator("modification_details", "knowledge_points", "risks")
    @classmethod
    def safe_document_lists(cls, value: list[str]) -> list[str]:
        return [_reject_controls(item, document=True) for item in value]

    @field_validator("suggested_tags")
    @classmethod
    def unique_sorted_tags(cls, value: list[str]) -> list[str]:
        for item in value:
            _reject_controls(item)
        if len(set(value)) != len(value):
            raise ValueError("suggested tags must be unique")
        return sorted(value)

    @field_validator("processed_at")
    @classmethod
    def utc_processed_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("processed_at must include a timezone")
        return value.astimezone(UTC)


class ImportJobCreate(BaseModel):
    model_config = _MODEL_CONFIG

    project_id: UUID
    local_task_id: LocalTaskId
    external_document_reference: ExternalReference | None = None
    base_sha256: str | None = None
    k3_result: K3Result
    attachments: list[AttachmentDeclaration] = Field(min_length=1, max_length=3)

    @field_validator("local_task_id", "external_document_reference", mode="before")
    @classmethod
    def normalize_identifiers(cls, value: object) -> object:
        return _normalize_text(value)

    @field_validator("local_task_id", "external_document_reference")
    @classmethod
    def safe_identifiers(cls, value: str | None) -> str | None:
        return None if value is None else _reject_controls(value)

    @field_validator("base_sha256")
    @classmethod
    def base_digest(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("base_sha256 must be 64 lowercase hexadecimal characters")
        return value

    @model_validator(mode="after")
    def normalize_and_validate_manifest(self) -> Self:
        kinds = [attachment.kind for attachment in self.attachments]
        if len(set(kinds)) != len(kinds):
            raise ValueError("attachment kinds must be unique")
        if kinds.count(AttachmentKind.K3_RAW) != 1:
            raise ValueError("exactly one K3_RAW attachment is required")
        if self.base_sha256 is not None and AttachmentKind.ORIGINAL not in kinds:
            raise ValueError("base_sha256 requires one ORIGINAL attachment")
        self.attachments = sorted(
            self.attachments,
            key=lambda attachment: _ATTACHMENT_ORDER[attachment.kind],
        )
        payload = self.model_dump(mode="json")
        compact_octets = len(_json_bytes(payload, separators=(",", ":")))
        postgres_octets = len(_json_bytes(payload, separators=(", ", ": ")))
        if max(compact_octets, postgres_octets) > MANIFEST_MAX_UTF8_BYTES:
            raise ValueError("canonical manifest exceeds UTF-8 JSON size budget")
        return self


def _json_bytes(payload: object, *, separators: tuple[str, str]) -> bytes:
    return json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=separators,
        sort_keys=True,
    ).encode("utf-8")


def canonical_manifest_bytes(manifest: ImportJobCreate) -> bytes:
    """Return the stable compact JSON representation used for the future fingerprint."""
    return _json_bytes(manifest.model_dump(mode="json"), separators=(",", ":"))


class ImportAttachmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    file_id: UUID
    upload_id: UUID
    kind: AttachmentKind
    file_state: FileState


class ImportJobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    project_id: UUID
    local_task_id: LocalTaskId
    external_document_reference: ExternalReference | None
    base_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    status: ImportStatus
    result_code: ResultCode | None
    k3_result: K3Result
    submitted_at: datetime | None
    created_at: datetime
    updated_at: datetime
    attachments: tuple[ImportAttachmentRead, ...] = Field(min_length=1, max_length=3)


class OwnerImportJobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    project_id: UUID
    local_task_id: LocalTaskId
    external_document_reference: ExternalReference | None
    model_label: ModelLabel
    status: ImportStatus
    result_code: ResultCode | None
    submitted_at: datetime | None
    created_at: datetime
    updated_at: datetime
    attachments: tuple[ImportAttachmentRead, ...] = Field(min_length=1, max_length=3)


class ImportPartUrlRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: PartUrl

    @field_validator("url")
    @classmethod
    def safe_url(cls, value: str) -> str:
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("part URL must contain no control characters")
        return value
