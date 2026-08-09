"""Blocking boto3 boundary behavior for the asynchronous object-storage adapter."""

import inspect
import threading
from dataclasses import dataclass, field
from typing import Any

import pytest

from superboss.infrastructure.s3 import Boto3ObjectStorage
from superboss.modules.files.storage import CompletedPart, ObjectMetadata


@dataclass
class RecordingS3Client:
    """A complete local boto3 surface used by the adapter, never a network client."""

    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    thread_ids: list[int] = field(default_factory=list)

    def _record(self, operation: str, **kwargs: Any) -> None:
        self.calls.append((operation, kwargs))
        self.thread_ids.append(threading.get_ident())

    def create_multipart_upload(self, **kwargs: Any) -> dict[str, str]:
        self._record("create_multipart_upload", **kwargs)
        return {"UploadId": "upload-123"}

    def generate_presigned_url(self, operation: str, **kwargs: Any) -> str:
        self._record(f"presign:{operation}", **kwargs)
        return f"https://signed.example/{operation}"

    def complete_multipart_upload(self, **kwargs: Any) -> dict[str, object]:
        self._record("complete_multipart_upload", **kwargs)
        return {}

    def head_object(self, **kwargs: Any) -> dict[str, object]:
        self._record("head_object", **kwargs)
        return {"ContentLength": 42, "ETag": '"object-etag"'}

    def abort_multipart_upload(self, **kwargs: Any) -> dict[str, object]:
        self._record("abort_multipart_upload", **kwargs)
        return {}

    def delete_object(self, **kwargs: Any) -> dict[str, object]:
        self._record("delete_object", **kwargs)
        return {}

    def list_multipart_uploads(self, **kwargs: Any) -> dict[str, object]:
        self._record("list_multipart_uploads", **kwargs)
        return {
            "Uploads": [
                {"Key": "projects/p/x.pdf", "UploadId": "upload-a"},
                {"Key": "projects/p/x.pdf.bak", "UploadId": "must-not-adopt"},
            ]
        }


@dataclass
class StreamingBody:
    chunks: list[bytes]
    error_on_read: int | None = None
    read_calls: int = 0
    close_calls: int = 0
    thread_ids: list[int] = field(default_factory=list)

    def read(self, _size: int) -> bytes:
        self.read_calls += 1
        self.thread_ids.append(threading.get_ident())
        if self.error_on_read == self.read_calls:
            raise RuntimeError("read failure")
        return self.chunks.pop(0) if self.chunks else b""

    def close(self) -> None:
        self.close_calls += 1
        self.thread_ids.append(threading.get_ident())


@dataclass
class StreamingS3Client:
    body: StreamingBody
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    thread_ids: list[int] = field(default_factory=list)

    def get_object(self, **kwargs: Any) -> dict[str, StreamingBody]:
        self.calls.append(("get_object", kwargs))
        self.thread_ids.append(threading.get_ident())
        return {"Body": self.body}


@pytest.mark.asyncio
async def test_boto3_adapter_maps_multipart_operations_and_runs_blocking_client_off_loop() -> None:
    """Wrong boto arguments or a direct blocking call must fail this boundary contract."""
    client = RecordingS3Client()
    storage = Boto3ObjectStorage(bucket="files-bucket", client=client)  # type: ignore[arg-type]
    main_thread = threading.get_ident()

    assert await storage.create_multipart("projects/p/x.pdf", "application/pdf") == "upload-123"
    assert await storage.presign_upload_part("projects/p/x.pdf", "upload-123", 7, 900) == "https://signed.example/upload_part"
    metadata = await storage.complete_multipart(
        "projects/p/x.pdf",
        "upload-123",
        [CompletedPart(part_number=2, etag="etag-2"), CompletedPart(part_number=7, etag="etag-7")],
    )
    await storage.abort_multipart("projects/p/x.pdf", "upload-123")
    assert await storage.presign_get("projects/p/x.pdf", 60) == "https://signed.example/get_object"

    assert metadata == ObjectMetadata(size_bytes=42, etag='"object-etag"')
    assert client.calls == [
        ("create_multipart_upload", {"Bucket": "files-bucket", "Key": "projects/p/x.pdf", "ContentType": "application/pdf"}),
        ("presign:upload_part", {"Params": {"Bucket": "files-bucket", "Key": "projects/p/x.pdf", "UploadId": "upload-123", "PartNumber": 7}, "ExpiresIn": 900}),
        ("complete_multipart_upload", {"Bucket": "files-bucket", "Key": "projects/p/x.pdf", "UploadId": "upload-123", "MultipartUpload": {"Parts": [{"PartNumber": 2, "ETag": "etag-2"}, {"PartNumber": 7, "ETag": "etag-7"}]}}),
        ("head_object", {"Bucket": "files-bucket", "Key": "projects/p/x.pdf"}),
        ("abort_multipart_upload", {"Bucket": "files-bucket", "Key": "projects/p/x.pdf", "UploadId": "upload-123"}),
        ("presign:get_object", {"Params": {"Bucket": "files-bucket", "Key": "projects/p/x.pdf"}, "ExpiresIn": 60}),
    ]
    assert client.thread_ids and all(thread_id != main_thread for thread_id in client.thread_ids)


@pytest.mark.asyncio
async def test_boto3_adapter_lists_only_exact_key_multipart_ids_off_loop() -> None:
    """Prefix-neighbor uploads must never be adopted during provisioning recovery."""
    client = RecordingS3Client()
    storage = Boto3ObjectStorage(bucket="files-bucket", client=client)  # type: ignore[arg-type]
    main_thread = threading.get_ident()

    assert await storage.list_multipart_uploads("projects/p/x.pdf") == ["upload-a"]
    assert client.calls == [
        (
            "list_multipart_uploads",
            {"Bucket": "files-bucket", "Prefix": "projects/p/x.pdf"},
        )
    ]
    assert client.thread_ids and all(thread_id != main_thread for thread_id in client.thread_ids)


@pytest.mark.asyncio
async def test_boto3_adapter_deletes_exact_object_off_loop() -> None:
    """Compensation deletion must not run the blocking client on the event loop."""
    client = RecordingS3Client()
    storage = Boto3ObjectStorage(bucket="files-bucket", client=client)  # type: ignore[arg-type]
    main_thread = threading.get_ident()

    await storage.delete_object("projects/p/x.pdf")

    assert client.calls == [("delete_object", {"Bucket": "files-bucket", "Key": "projects/p/x.pdf"})]
    assert client.thread_ids == [thread_id for thread_id in client.thread_ids if thread_id != main_thread]


@pytest.mark.parametrize(
    ("method", "parameters"),
    [
        ("create_multipart", ("object_key", "content_type")),
        ("list_multipart_uploads", ("object_key",)),
        ("stat_object", ("object_key",)),
        ("delete_object", ("object_key",)),
        ("presign_upload_part", ("object_key", "multipart_id", "part_number", "expires_seconds")),
        ("complete_multipart", ("object_key", "multipart_id", "parts")),
        ("abort_multipart", ("object_key", "multipart_id")),
        ("presign_get", ("object_key", "expires_seconds")),
        ("stream", ("object_key",)),
    ],
)
def test_boto3_adapter_public_methods_match_object_storage_parameter_contract(
    method: str, parameters: tuple[str, ...]
) -> None:
    """Renaming public object-key parameters breaks protocol-compatible keyword callers."""
    signature = inspect.signature(getattr(Boto3ObjectStorage, method))
    assert tuple(signature.parameters)[1:] == parameters


@pytest.mark.asyncio
async def test_boto3_stream_yields_every_chunk_and_closes_body_after_eof() -> None:
    """Removing finalization would leak the boto streaming body after normal consumption."""
    body = StreamingBody([b"first", b"second"])
    client = StreamingS3Client(body)
    storage = Boto3ObjectStorage(bucket="files-bucket", client=client)  # type: ignore[arg-type]
    main_thread = threading.get_ident()

    chunks = [chunk async for chunk in storage.stream("projects/p/x.pdf")]

    assert chunks == [b"first", b"second"]
    assert client.calls == [("get_object", {"Bucket": "files-bucket", "Key": "projects/p/x.pdf"})]
    assert body.close_calls == 1
    assert all(thread_id != main_thread for thread_id in client.thread_ids + body.thread_ids)


@pytest.mark.asyncio
async def test_boto3_stream_closes_body_once_when_read_raises() -> None:
    """A read failure must remain visible while still releasing the boto response body."""
    body = StreamingBody([b"first"], error_on_read=2)
    client = StreamingS3Client(body)
    storage = Boto3ObjectStorage(bucket="files-bucket", client=client)  # type: ignore[arg-type]
    main_thread = threading.get_ident()

    with pytest.raises(RuntimeError, match="read failure"):
        _ = [chunk async for chunk in storage.stream("projects/p/x.pdf")]

    assert body.close_calls == 1
    assert all(thread_id != main_thread for thread_id in client.thread_ids + body.thread_ids)


@pytest.mark.asyncio
async def test_boto3_stream_closes_body_once_when_consumer_stops_early() -> None:
    """Calling aclose after the first chunk must release the boto body exactly once."""
    body = StreamingBody([b"first", b"second"])
    client = StreamingS3Client(body)
    storage = Boto3ObjectStorage(bucket="files-bucket", client=client)  # type: ignore[arg-type]
    main_thread = threading.get_ident()
    stream = storage.stream("projects/p/x.pdf")

    assert await anext(stream) == b"first"
    await stream.aclose()

    assert body.close_calls == 1
    assert all(thread_id != main_thread for thread_id in client.thread_ids + body.thread_ids)
