"""Strict no-redirect HTTP client for the Task 9/10 device API."""

from __future__ import annotations

import ipaddress
import json
import re
import socket
from datetime import datetime
from typing import Annotated, Any, Literal, Self
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from .config import (
    ETAG_MAX_CHARS,
    HTTP_TIMEOUT_SECONDS,
    RESPONSE_MAX_BYTES,
    TOKEN_MAX_CHARS,
)
from .credentials import CredentialStore
from .errors import CREDENTIAL_ERROR, SERVER_REJECTED, TEMPORARY_FAILURE, ConnectorError
from .manifest import AttachmentKind, K3Result, ServerManifest, server_payload

_STRICT = ConfigDict(extra="forbid", hide_input_in_errors=True)
ResultCode = Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")]


class TokenResponse(BaseModel):
    model_config = _STRICT

    access_token: str = Field(min_length=1, max_length=TOKEN_MAX_CHARS)
    refresh_token: str = Field(min_length=1, max_length=TOKEN_MAX_CHARS)
    token_type: Literal["bearer"]
    expires_at: datetime
    refresh_expires_at: datetime

    @field_validator("access_token", "refresh_token")
    @classmethod
    def safe_token(cls, value: str) -> str:
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as error:
            raise ValueError("unsafe token") from error
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("unsafe token")
        return value

    @field_validator("expires_at", "refresh_expires_at")
    @classmethod
    def aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timezone required")
        return value


class AttachmentResponse(BaseModel):
    model_config = _STRICT

    id: UUID
    file_id: UUID
    upload_id: UUID
    kind: AttachmentKind
    file_state: Literal["UPLOADING", "QUARANTINED", "SCANNING", "CLEAN", "INFECTED", "FAILED"]


class JobResponse(BaseModel):
    model_config = _STRICT

    id: UUID
    project_id: UUID
    local_task_id: str = Field(min_length=1, max_length=255)
    external_document_reference: Annotated[str, Field(min_length=1, max_length=1_024)] | None
    base_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None
    status: Literal["UPLOADING", "SCANNING", "RECEIVED", "REJECTED", "CONFLICT"]
    result_code: ResultCode | None
    k3_result: K3Result
    submitted_at: datetime | None
    created_at: datetime
    updated_at: datetime
    attachments: tuple[AttachmentResponse, ...] = Field(min_length=1, max_length=3)

    @field_validator("created_at", "updated_at", "submitted_at")
    @classmethod
    def aware_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("timezone required")
        return value

    @model_validator(mode="after")
    def consistent_status(self) -> Self:
        submitted = self.submitted_at is not None
        rejected = self.status in {"REJECTED", "CONFLICT"}
        if (
            self.updated_at < self.created_at
            or (
                self.submitted_at is not None
                and not self.created_at <= self.submitted_at <= self.updated_at
            )
            or (self.status == "UPLOADING" and submitted)
            or (self.status != "UPLOADING" and not submitted)
            or rejected != (self.result_code is not None)
        ):
            raise ValueError("invalid job response semantics")
        return self


class SubmitResponse(BaseModel):
    model_config = _STRICT

    id: UUID
    status: Literal["SCANNING", "RECEIVED", "REJECTED", "CONFLICT"]
    result_code: ResultCode | None
    submitted_at: datetime
    updated_at: datetime

    @field_validator("submitted_at", "updated_at")
    @classmethod
    def aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timezone required")
        return value

    @model_validator(mode="after")
    def consistent_status(self) -> Self:
        rejected = self.status in {"REJECTED", "CONFLICT"}
        if rejected != (self.result_code is not None) or self.updated_at < self.submitted_at:
            raise ValueError("invalid submit response semantics")
        return self


class PartUrlResponse(BaseModel):
    model_config = _STRICT

    url: str = Field(min_length=1, max_length=4_096)

    @field_validator("url")
    @classmethod
    def safe_url(cls, value: str) -> str:
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("unsafe URL")
        try:
            parsed = urlsplit(value)
            port = parsed.port
        except ValueError as error:
            raise ValueError("unsafe URL") from error
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.fragment
            or port == 0
        ):
            raise ValueError("unsafe URL")
        return value


def _json[T: BaseModel](payload: bytes, model: type[T], *, exit_code: int) -> T:
    try:
        return model.model_validate(json.loads(payload))
    except (RecursionError, UnicodeError, ValueError, ValidationError) as error:
        message = CREDENTIAL_ERROR if exit_code == 3 else SERVER_REJECTED
        raise ConnectorError(exit_code, message) from error


class ApiClient:
    def __init__(self, origin: str) -> None:
        self.origin = origin
        self._client = httpx.Client(
            follow_redirects=False,
            trust_env=False,
            timeout=httpx.Timeout(HTTP_TIMEOUT_SECONDS),
            headers={"Accept-Encoding": "identity"},
        )
        self._upload_client = httpx.Client(
            follow_redirects=False,
            trust_env=False,
            timeout=httpx.Timeout(HTTP_TIMEOUT_SECONDS),
        )

    def close(self) -> None:
        self._client.close()
        self._upload_client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        access_token: str | None = None,
        json_body: object | None = None,
        headers: dict[str, str] | None = None,
        auth_failure: bool = False,
    ) -> bytes:
        request_headers = dict(headers or {})
        if access_token is not None:
            request_headers["Authorization"] = f"Bearer {access_token}"
        try:
            with self._client.stream(
                method,
                f"{self.origin}{path}",
                json=json_body,
                headers=request_headers,
            ) as response:
                if response.status_code == 429 or response.status_code >= 500:
                    raise ConnectorError(6, TEMPORARY_FAILURE)
                if not 200 <= response.status_code < 300:
                    if auth_failure:
                        raise ConnectorError(3, CREDENTIAL_ERROR)
                    raise ConnectorError(5, SERVER_REJECTED)
                limit_exit = 3 if auth_failure else 5
                content_encoding = response.headers.get("Content-Encoding")
                if content_encoding is not None and content_encoding.strip().lower() != "identity":
                    raise ConnectorError(
                        limit_exit,
                        CREDENTIAL_ERROR if auth_failure else SERVER_REJECTED,
                    )
                content_length = response.headers.get("Content-Length")
                if content_length is not None:
                    try:
                        parsed_length = int(content_length)
                        if parsed_length < 0 or parsed_length > RESPONSE_MAX_BYTES:
                            raise ConnectorError(
                                limit_exit,
                                CREDENTIAL_ERROR if auth_failure else SERVER_REJECTED,
                            )
                    except ValueError as error:
                        raise ConnectorError(
                            limit_exit,
                            CREDENTIAL_ERROR if auth_failure else SERVER_REJECTED,
                        ) from error
                payload = bytearray()
                for chunk in response.iter_raw(chunk_size=RESPONSE_MAX_BYTES + 1):
                    remaining = RESPONSE_MAX_BYTES + 1 - len(payload)
                    payload.extend(chunk[:remaining])
                    if len(payload) > RESPONSE_MAX_BYTES or len(chunk) > remaining:
                        raise ConnectorError(
                            limit_exit,
                            CREDENTIAL_ERROR if auth_failure else SERVER_REJECTED,
                        )
                return bytes(payload)
        except ConnectorError:
            raise
        except httpx.RequestError as error:
            raise ConnectorError(6, TEMPORARY_FAILURE) from error

    def pair(self, code: str, name: str, credentials: CredentialStore) -> None:
        payload = self._request(
            "POST",
            "/api/v1/device-auth/pair",
            json_body={"pairing_code": code, "device_name": name},
            auth_failure=True,
        )
        token = _json(payload, TokenResponse, exit_code=3)
        credentials.save_refresh(token.refresh_token)

    def refresh(self, credentials: CredentialStore) -> str:
        refresh_token = credentials.load_refresh()
        payload = self._request(
            "POST",
            "/api/v1/device-auth/refresh",
            json_body={"refresh_token": refresh_token},
            auth_failure=True,
        )
        token = _json(payload, TokenResponse, exit_code=3)
        credentials.save_refresh(token.refresh_token)
        return token.access_token

    def create_job(
        self,
        manifest: ServerManifest,
        idempotency_key: str,
        access_token: str,
    ) -> JobResponse:
        payload = self._request(
            "POST",
            "/api/v1/device/import-jobs",
            access_token=access_token,
            json_body=server_payload(manifest),
            headers={"Idempotency-Key": idempotency_key},
        )
        parsed = _json(payload, JobResponse, exit_code=5)
        expected = server_payload(manifest)
        actual = parsed.model_dump(mode="json")
        for field in (
            "project_id",
            "local_task_id",
            "external_document_reference",
            "base_sha256",
            "k3_result",
        ):
            if actual[field] != expected[field]:
                raise ConnectorError(5, SERVER_REJECTED)
        expected_kinds = [item.kind for item in manifest.attachments]
        actual_kinds = [item.kind for item in parsed.attachments]
        if actual_kinds != expected_kinds or len(set(actual_kinds)) != len(actual_kinds):
            raise ConnectorError(5, SERVER_REJECTED)
        identifiers = (
            [item.id for item in parsed.attachments],
            [item.file_id for item in parsed.attachments],
            [item.upload_id for item in parsed.attachments],
        )
        if (
            parsed.status != "UPLOADING"
            or parsed.submitted_at is not None
            or parsed.result_code is not None
            or any(item.file_state != "UPLOADING" for item in parsed.attachments)
            or any(len(values) != len(set(values)) for values in identifiers)
        ):
            raise ConnectorError(5, SERVER_REJECTED)
        return parsed

    def part_url(
        self,
        job_id: UUID,
        attachment_id: UUID,
        part_number: int,
        access_token: str,
    ) -> str:
        payload = self._request(
            "POST",
            f"/api/v1/device/import-jobs/{job_id}/attachments/{attachment_id}/parts/{part_number}",
            access_token=access_token,
        )
        parsed = _json(payload, PartUrlResponse, exit_code=5)
        return parsed.url

    @staticmethod
    def _validate_upload_destination(url: str) -> None:
        try:
            parsed = urlsplit(url)
            host = parsed.hostname
            port = parsed.port or 443
            if host is None or host.lower() == "localhost":
                raise ValueError("unsafe destination")
            try:
                addresses = [ipaddress.ip_address(host)]
            except ValueError:
                if re.fullmatch(r"[0-9.]+", host) is not None:
                    raise ValueError("noncanonical numeric destination")
                records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
                addresses = [ipaddress.ip_address(record[4][0]) for record in records]
            if not addresses or any(
                not address.is_global
                or address.is_loopback
                or address.is_link_local
                or address.is_multicast
                or address.is_unspecified
                or address.is_reserved
                or address.is_private
                for address in addresses
            ):
                raise ValueError("unsafe destination")
        except socket.gaierror as error:
            raise ConnectorError(6, TEMPORARY_FAILURE) from error
        except OSError as error:
            raise ConnectorError(6, TEMPORARY_FAILURE) from error
        except (TypeError, ValueError) as error:
            raise ConnectorError(5, SERVER_REJECTED) from error

    def put_part(self, url: str, content: bytes) -> str:
        self._validate_upload_destination(url)
        self._upload_client.cookies.clear()
        try:
            with self._upload_client.stream("PUT", url, content=content) as response:
                if response.status_code == 429 or response.status_code >= 500:
                    raise ConnectorError(6, TEMPORARY_FAILURE)
                if not 200 <= response.status_code < 300:
                    raise ConnectorError(5, SERVER_REJECTED)
                raw_etag = response.headers.get("ETag")
                etag = "" if raw_etag is None else str(raw_etag).strip()
        except httpx.RequestError as error:
            raise ConnectorError(6, TEMPORARY_FAILURE) from error
        if not 1 <= len(etag) <= ETAG_MAX_CHARS or any(
            ord(character) < 32 or ord(character) == 127 for character in etag
        ):
            raise ConnectorError(5, SERVER_REJECTED)
        return etag

    def complete_attachment(
        self,
        job_id: UUID,
        expected: AttachmentResponse,
        parts: list[dict[str, Any]],
        access_token: str,
    ) -> AttachmentResponse:
        payload = self._request(
            "POST",
            f"/api/v1/device/import-jobs/{job_id}/attachments/{expected.id}/complete",
            access_token=access_token,
            json_body={"parts": parts},
        )
        parsed = _json(payload, AttachmentResponse, exit_code=5)
        if (
            parsed.id != expected.id
            or parsed.file_id != expected.file_id
            or parsed.upload_id != expected.upload_id
            or parsed.kind != expected.kind
            or parsed.file_state == "UPLOADING"
        ):
            raise ConnectorError(5, SERVER_REJECTED)
        return parsed

    def submit_job(self, job_id: UUID, access_token: str) -> SubmitResponse:
        payload = self._request(
            "POST",
            f"/api/v1/device/import-jobs/{job_id}/submit",
            access_token=access_token,
        )
        parsed = _json(payload, SubmitResponse, exit_code=5)
        if parsed.id != job_id:
            raise ConnectorError(5, SERVER_REJECTED)
        return parsed

    def status(self, job_id: UUID, access_token: str) -> JobResponse:
        payload = self._request(
            "GET",
            f"/api/v1/device/import-jobs/{job_id}",
            access_token=access_token,
        )
        parsed = _json(payload, JobResponse, exit_code=5)
        if parsed.id != job_id:
            raise ConnectorError(5, SERVER_REJECTED)
        return parsed
