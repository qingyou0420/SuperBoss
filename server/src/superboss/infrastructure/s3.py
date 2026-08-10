"""Async wrapper around the blocking boto3 S3 client."""

import asyncio
from collections.abc import AsyncIterator
from threading import Lock

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from mypy_boto3_s3 import S3Client
from mypy_boto3_s3.type_defs import ListMultipartUploadsOutputTypeDef

from superboss.modules.files.storage import CompletedPart, ObjectMetadata


class MultipartPaginationError(RuntimeError):
    """The provider returned a truncated multipart listing without forward progress."""


class Boto3ObjectStorage:
    def __init__(
        self,
        bucket: str,
        endpoint_url: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        client: S3Client | None = None,
        public_endpoint_url: str | None = None,
        public_client: S3Client | None = None,
    ) -> None:
        self.bucket = bucket
        self._client = client
        self._endpoint_url = endpoint_url
        self._access_key_id = access_key_id
        self._secret_access_key = secret_access_key
        self._client_lock = Lock()
        self._public_endpoint_url = public_endpoint_url
        self._public_client = public_client
        self._public_client_lock = Lock()

    def _build_client(self, endpoint_url: str | None) -> S3Client:
        return boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=self._access_key_id or None,
            aws_secret_access_key=self._secret_access_key or None,
            config=Config(
                signature_version="s3v4",
                connect_timeout=5,
                read_timeout=10,
                retries={"max_attempts": 2},
            ),
        )

    @property
    def client(self) -> S3Client:
        client = self._client
        if client is not None:
            return client
        with self._client_lock:
            client = self._client
            if client is None:
                client = self._build_client(self._endpoint_url)
                self._client = client
        return client

    @property
    def presign_client(self) -> S3Client:
        if self._public_endpoint_url is None:
            return self.client
        client = self._public_client
        if client is not None:
            return client
        with self._public_client_lock:
            client = self._public_client
            if client is None:
                client = self._build_client(self._public_endpoint_url)
                self._public_client = client
        return client

    def _list_multipart_uploads_page(
        self,
        object_key: str,
        key_marker: str | None = None,
        upload_id_marker: str | None = None,
    ) -> ListMultipartUploadsOutputTypeDef:
        if key_marker is not None and upload_id_marker is not None:
            return self.client.list_multipart_uploads(
                Bucket=self.bucket,
                Prefix=object_key,
                KeyMarker=key_marker,
                UploadIdMarker=upload_id_marker,
            )
        return self.client.list_multipart_uploads(Bucket=self.bucket, Prefix=object_key)

    async def create_multipart(self, object_key: str, content_type: str) -> str:
        result = await asyncio.to_thread(
            lambda: self.client.create_multipart_upload(
                Bucket=self.bucket,
                Key=object_key,
                ContentType=content_type,
            )
        )
        return str(result["UploadId"])

    async def list_multipart_uploads(self, object_key: str) -> list[str]:
        multipart_ids: set[str] = set()
        seen_markers: set[tuple[str, str]] = set()
        key_marker: str | None = None
        upload_id_marker: str | None = None
        while True:
            result = await asyncio.to_thread(
                self._list_multipart_uploads_page,
                object_key,
                key_marker,
                upload_id_marker,
            )
            multipart_ids.update(
                str(upload["UploadId"])
                for upload in result.get("Uploads", [])
                if upload.get("Key") == object_key and upload.get("UploadId") is not None
            )
            if not result.get("IsTruncated", False):
                return sorted(multipart_ids)
            next_key_marker = result.get("NextKeyMarker")
            next_upload_id_marker = result.get("NextUploadIdMarker")
            if not isinstance(next_key_marker, str) or not isinstance(next_upload_id_marker, str):
                raise MultipartPaginationError("invalid multipart pagination")
            next_markers = (next_key_marker, next_upload_id_marker)
            if next_markers in seen_markers:
                raise MultipartPaginationError("invalid multipart pagination")
            seen_markers.add(next_markers)
            key_marker, upload_id_marker = next_markers

    async def stat_object(self, object_key: str) -> ObjectMetadata | None:
        try:
            head = await asyncio.to_thread(
                lambda: self.client.head_object(Bucket=self.bucket, Key=object_key)
            )
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise
        return ObjectMetadata(int(head["ContentLength"]), head.get("ETag"))

    async def delete_object(self, object_key: str) -> None:
        await asyncio.to_thread(
            lambda: self.client.delete_object(Bucket=self.bucket, Key=object_key)
        )

    async def presign_upload_part(
        self, object_key: str, multipart_id: str, part_number: int, expires_seconds: int
    ) -> str:
        return await asyncio.to_thread(
            lambda: self.presign_client.generate_presigned_url(
                "upload_part",
                Params={
                    "Bucket": self.bucket,
                    "Key": object_key,
                    "UploadId": multipart_id,
                    "PartNumber": part_number,
                },
                ExpiresIn=expires_seconds,
            )
        )

    async def complete_multipart(
        self, object_key: str, multipart_id: str, parts: list[CompletedPart]
    ) -> ObjectMetadata:
        await asyncio.to_thread(
            lambda: self.client.complete_multipart_upload(
                Bucket=self.bucket,
                Key=object_key,
                UploadId=multipart_id,
                MultipartUpload={
                    "Parts": [{"PartNumber": part.part_number, "ETag": part.etag} for part in parts]
                },
            )
        )
        head = await asyncio.to_thread(
            lambda: self.client.head_object(Bucket=self.bucket, Key=object_key)
        )
        return ObjectMetadata(int(head["ContentLength"]), head.get("ETag"))

    async def abort_multipart(self, object_key: str, multipart_id: str) -> None:
        try:
            await asyncio.to_thread(
                lambda: self.client.abort_multipart_upload(
                    Bucket=self.bucket,
                    Key=object_key,
                    UploadId=multipart_id,
                )
            )
        except ClientError as error:
            code = error.response.get("Error", {}).get("Code")
            if code not in {"NoSuchUpload", "404", "NotFound"}:
                raise

    async def presign_get(self, object_key: str, expires_seconds: int) -> str:
        return await asyncio.to_thread(
            lambda: self.presign_client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": object_key},
                ExpiresIn=expires_seconds,
            )
        )

    async def stream(self, object_key: str) -> AsyncIterator[bytes]:
        body = (
            await asyncio.to_thread(
                lambda: self.client.get_object(Bucket=self.bucket, Key=object_key)
            )
        )["Body"]
        try:
            while chunk := await asyncio.to_thread(body.read, 64 * 1024):
                yield chunk
        finally:
            await asyncio.to_thread(body.close)
