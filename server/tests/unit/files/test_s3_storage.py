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


@pytest.mark.parametrize(
    ("method", "parameters"),
    [
        ("create_multipart", ("object_key", "content_type")),
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
