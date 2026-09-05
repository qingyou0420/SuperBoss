import re
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from superboss.modules.files.models import FileState, FolderVisibility


class UploadStart(BaseModel):
    folder_id: UUID
    filename: str = Field(min_length=1, max_length=1024)
    size_bytes: int = Field(ge=1, le=100 * 1024 * 1024)
    sha256: str
    content_type: str = Field(default="application/octet-stream", max_length=255)
    project_id: UUID | None = None

    @field_validator("sha256")
    @classmethod
    def digest(cls, v: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{64}", v):
            raise ValueError("sha256 must be 64 lowercase hexadecimal characters")
        return v

    @field_validator("filename")
    @classmethod
    def safe_text(cls, value: str) -> str:
        if not value.strip() or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("must be nonblank and contain no control characters")
        return value

    @field_validator("content_type")
    @classmethod
    def safe_content_type(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+", value):
            raise ValueError("content_type must be a MIME type")
        return value


class PartComplete(BaseModel):
    part_number: int = Field(ge=1, le=10000)
    etag: str = Field(min_length=1, max_length=1024)

    @field_validator("etag")
    @classmethod
    def safe_etag(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("etag must contain no control characters")
        return value


class UploadComplete(BaseModel):
    parts: list[PartComplete] = Field(min_length=1)

    @field_validator("parts")
    @classmethod
    def unique_parts(cls, value: list[PartComplete]) -> list[PartComplete]:
        if len({part.part_number for part in value}) != len(value):
            raise ValueError("part numbers must be unique")
        return sorted(value, key=lambda part: part.part_number)


class FolderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parent_id: UUID
    name: str = Field(min_length=1, max_length=128)

    @field_validator("name")
    @classmethod
    def canonical_name(cls, value: str) -> str:
        normalized = value.strip(" \t\r\n\u00a0")
        if not 1 <= len(normalized) <= 128:
            raise ValueError("folder name must contain 1 to 128 characters")
        return normalized


class FolderRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    parent_id: UUID | None
    name: str
    visibility: FolderVisibility


class FileRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    folder_id: UUID
    project_id: UUID | None
    filename: str
    size_bytes: int
    content_type: str
    state: FileState
    created_at: datetime


class FilePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str | None = Field(default=None, min_length=1, max_length=1024)
    folder_id: UUID | None = None

    @field_validator("filename")
    @classmethod
    def safe_filename(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip() or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("filename must contain no control characters")
        return value.strip()
