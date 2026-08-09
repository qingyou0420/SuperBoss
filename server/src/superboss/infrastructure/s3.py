"""Async wrapper around the blocking boto3 S3 client."""

import asyncio
from collections.abc import AsyncIterator

import boto3
from mypy_boto3_s3 import S3Client

from superboss.modules.files.storage import CompletedPart, ObjectMetadata


class Boto3ObjectStorage:
    def __init__(
        self,
        bucket: str,
        endpoint_url: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        client: S3Client | None = None,
    ) -> None:
        self.bucket = bucket
        self.client = client or boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key_id or None,
            aws_secret_access_key=secret_access_key or None,
        )

    async def create_multipart(self, object_key: str, content_type: str) -> str:
        result = await asyncio.to_thread(
            self.client.create_multipart_upload,
            Bucket=self.bucket,
            Key=object_key,
            ContentType=content_type,
        )
        return str(result["UploadId"])

    async def presign_upload_part(
        self, object_key: str, multipart_id: str, part_number: int, expires_seconds: int
    ) -> str:
        return await asyncio.to_thread(
            self.client.generate_presigned_url,
            "upload_part",
            Params={
                "Bucket": self.bucket,
                "Key": object_key,
                "UploadId": multipart_id,
                "PartNumber": part_number,
            },
            ExpiresIn=expires_seconds,
        )

    async def complete_multipart(
        self, object_key: str, multipart_id: str, parts: list[CompletedPart]
    ) -> ObjectMetadata:
        await asyncio.to_thread(lambda: self.client.complete_multipart_upload(Bucket=self.bucket, Key=object_key, UploadId=multipart_id, MultipartUpload={"Parts": [{"PartNumber": p.part_number, "ETag": p.etag} for p in parts]}))
        head = await asyncio.to_thread(self.client.head_object, Bucket=self.bucket, Key=object_key)
        return ObjectMetadata(int(head["ContentLength"]), head.get("ETag"))

    async def abort_multipart(self, object_key: str, multipart_id: str) -> None:
        await asyncio.to_thread(
            self.client.abort_multipart_upload, Bucket=self.bucket, Key=object_key, UploadId=multipart_id
        )

    async def presign_get(self, object_key: str, expires_seconds: int) -> str:
        return await asyncio.to_thread(
            self.client.generate_presigned_url,
            "get_object",
            Params={"Bucket": self.bucket, "Key": object_key},
            ExpiresIn=expires_seconds,
        )

    async def stream(self, object_key: str) -> AsyncIterator[bytes]:
        body = (await asyncio.to_thread(self.client.get_object, Bucket=self.bucket, Key=object_key))[
            "Body"
        ]
        while chunk := await asyncio.to_thread(body.read, 64 * 1024):
            yield chunk
