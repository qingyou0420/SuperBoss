import re
from datetime import date
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class UploadStart(BaseModel):
    project_id: UUID
    filename: str = Field(min_length=1, max_length=1024)
    size_bytes: int = Field(ge=1, le=100 * 1024 * 1024)
    sha256: str
    category: str = Field(min_length=1, max_length=255)
    file_date: date
    content_type: str = "application/octet-stream"

    @field_validator("sha256")
    @classmethod
    def digest(cls, v: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{64}", v):
            raise ValueError("sha256 must be 64 lowercase hexadecimal characters")
        return v


class PartComplete(BaseModel):
    part_number: int = Field(ge=1, le=10000)
    etag: str = Field(min_length=1, max_length=1024)


class UploadComplete(BaseModel):
    parts: list[PartComplete] = Field(min_length=1)
