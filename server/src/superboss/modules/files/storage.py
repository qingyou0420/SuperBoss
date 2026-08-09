from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class CompletedPart:
    part_number: int
    etag: str


@dataclass(frozen=True)
class ObjectMetadata:
    size_bytes: int
    etag: str | None = None


class ObjectStorage(Protocol):
    async def create_multipart(self, object_key: str, content_type: str) -> str: ...
    async def list_multipart_uploads(self, object_key: str) -> list[str]: ...
    async def stat_object(self, object_key: str) -> ObjectMetadata | None: ...
    async def presign_upload_part(
        self, object_key: str, multipart_id: str, part_number: int, expires_seconds: int
    ) -> str: ...
    async def complete_multipart(
        self, object_key: str, multipart_id: str, parts: list[CompletedPart]
    ) -> ObjectMetadata: ...
    async def abort_multipart(self, object_key: str, multipart_id: str) -> None: ...
    async def presign_get(self, object_key: str, expires_seconds: int) -> str: ...
    def stream(self, object_key: str) -> AsyncIterator[bytes]: ...
