"""Behavioral in-memory object storage for file service tests."""
import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from uuid import uuid4

from superboss.modules.files.storage import CompletedPart, ObjectMetadata


@dataclass
class InMemoryObjectStorage:
    complete_size: int = 1
    active: dict[str, str] = field(default_factory=dict)
    completed: dict[str, list[CompletedPart]] = field(default_factory=dict)
    aborted: set[str] = field(default_factory=set)
    objects: dict[str, ObjectMetadata] = field(default_factory=dict)
    bodies: dict[str, bytes] = field(default_factory=dict)
    expiries: list[int] = field(default_factory=list)
    complete_error: Exception | None = None
    abort_error: Exception | None = None
    created_multipart_id: str | None = None
    create_barrier: asyncio.Barrier | None = None
    create_error_after_create: Exception | None = None
    list_error: Exception | None = None
    create_calls: int = 0
    complete_calls: int = 0
    list_calls: int = 0
    stat_error: Exception | None = None
    complete_late_success_delay: float | None = None
    late_completion_tasks: list[asyncio.Task[None]] = field(default_factory=list)
    delete_error: Exception | None = None
    deleted: list[str] = field(default_factory=list)

    async def create_multipart(self, object_key: str, content_type: str) -> str:
        self.create_calls += 1
        upload_id = self.created_multipart_id if self.created_multipart_id is not None else str(uuid4())
        self.active[upload_id] = object_key
        if self.create_barrier is not None:
            await self.create_barrier.wait()
        if self.create_error_after_create is not None:
            raise self.create_error_after_create
        return upload_id

    async def list_multipart_uploads(self, object_key: str) -> list[str]:
        self.list_calls += 1
        if self.list_error is not None:
            raise self.list_error
        return sorted(upload_id for upload_id, key in self.active.items() if key == object_key)

    async def stat_object(self, object_key: str) -> ObjectMetadata | None:
        if self.stat_error is not None:
            raise self.stat_error
        return self.objects.get(object_key)

    async def delete_object(self, object_key: str) -> None:
        if self.delete_error is not None:
            raise self.delete_error
        self.objects.pop(object_key, None)
        self.deleted.append(object_key)

    async def presign_upload_part(self, object_key: str, multipart_id: str, part_number: int, expires_seconds: int) -> str:
        self.expiries.append(expires_seconds)
        return f"memory://part/{multipart_id}/{part_number}"

    async def complete_multipart(self, object_key: str, multipart_id: str, parts: list[CompletedPart]) -> ObjectMetadata:
        self.complete_calls += 1
        if self.complete_late_success_delay is not None:
            async def complete_later() -> None:
                await asyncio.sleep(self.complete_late_success_delay)
                self.active.pop(multipart_id, None)
                self.completed[multipart_id] = parts
                self.objects[object_key] = ObjectMetadata(self.complete_size)

            self.late_completion_tasks.append(asyncio.create_task(complete_later()))
            raise TimeoutError("provider secret")
        if self.complete_error is not None:
            raise self.complete_error
        self.active.pop(multipart_id)
        self.completed[multipart_id] = parts
        metadata = ObjectMetadata(self.complete_size)
        self.objects[object_key] = metadata
        return metadata

    async def await_late_completions(self) -> None:
        tasks = self.late_completion_tasks
        self.late_completion_tasks = []
        if tasks:
            await asyncio.gather(*tasks)

    async def abort_multipart(self, object_key: str, multipart_id: str) -> None:
        self.active.pop(multipart_id, None)
        self.aborted.add(multipart_id)
        if self.abort_error is not None:
            raise self.abort_error

    async def presign_get(self, object_key: str, expires_seconds: int) -> str:
        self.expiries.append(expires_seconds)
        return f"memory://get/{object_key}"

    async def stream(self, object_key: str) -> AsyncIterator[bytes]:
        yield self.bodies.get(object_key, b"")
