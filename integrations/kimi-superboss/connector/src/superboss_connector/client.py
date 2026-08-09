"""Strict no-redirect HTTP client for the Task 9/10 device API."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal, Self
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .config import ETAG_MAX_CHARS, HTTP_TIMEOUT_SECONDS
from .credentials import CredentialStore
from .errors import CREDENTIAL_ERROR, SERVER_REJECTED, TEMPORARY_FAILURE, ConnectorError
from .manifest import AttachmentKind, K3Result, ServerManifest, server_payload

_STRICT = ConfigDict(extra="forbid", hide_input_in_errors=True)
ResultCode = Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")]


class TokenResponse(BaseModel):
    model_config = _STRICT

    access_token: str = Field(min_length=1)
    refresh_token: str = Field(min_length=1)
    token_type: Literal["bearer"]
    expires_at: datetime
    refresh_expires_at: datetime


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
    external_document_reference: str | None
    base_sha256: str | None
    status: Literal["UPLOADING", "SCANNING", "RECEIVED", "REJECTED", "CONFLICT"]
    result_code: ResultCode | None
    k3_result: K3Result
    submitted_at: datetime | None
    created_at: datetime
    updated_at: datetime
    attachments: tuple[AttachmentResponse, ...] = Field(min_length=1, max_length=3)


class SubmitResponse(BaseModel):
    model_config = _STRICT

    id: UUID
    status: Literal["UPLOADING", "SCANNING", "RECEIVED", "REJECTED", "CONFLICT"]
    result_code: ResultCode | None
    submitted_at: datetime
    updated_at: datetime


class PartUrlResponse(BaseModel):
    model_config = _STRICT

    url: str = Field(min_length=1, max_length=4_096)

    @field_validator("url")
    @classmethod
    def safe_url(cls, value: str) -> str:
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("unsafe URL")
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("unsafe URL")
        return value


def _json[T: BaseModel](response: httpx.Response, model: type[T], *, exit_code: int) -> T:
    try:
        return model.model_validate(response.json())
    except (ValueError, ValidationError) as error:
        message = CREDENTIAL_ERROR if exit_code == 3 else SERVER_REJECTED
        raise ConnectorError(exit_code, message) from error


class ApiClient:
    def __init__(self, origin: str) -> None:
        self.origin = origin
        self._client = httpx.Client(
            follow_redirects=False,
            trust_env=False,
            timeout=httpx.Timeout(HTTP_TIMEOUT_SECONDS),
        )

    def close(self) -> None:
        self._client.close()

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
    ) -> httpx.Response:
        request_headers = dict(headers or {})
        if access_token is not None:
            request_headers["Authorization"] = f"Bearer {access_token}"
        try:
            response = self._client.request(
                method,
                f"{self.origin}{path}",
                json=json_body,
                headers=request_headers,
            )
        except httpx.RequestError as error:
            raise ConnectorError(6, TEMPORARY_FAILURE) from error
        if response.status_code == 429 or response.status_code >= 500:
            raise ConnectorError(6, TEMPORARY_FAILURE)
        if not 200 <= response.status_code < 300:
            if auth_failure:
                raise ConnectorError(3, CREDENTIAL_ERROR)
            raise ConnectorError(5, SERVER_REJECTED)
        return response

    def pair(self, code: str, name: str, credentials: CredentialStore) -> None:
        response = self._request(
            "POST",
            "/api/v1/device-auth/pair",
            json_body={"pairing_code": code, "device_name": name},
            auth_failure=True,
        )
        token = _json(response, TokenResponse, exit_code=3)
        credentials.save_refresh(token.refresh_token)

    def refresh(self, credentials: CredentialStore) -> str:
        refresh_token = credentials.load_refresh()
        response = self._request(
            "POST",
            "/api/v1/device-auth/refresh",
            json_body={"refresh_token": refresh_token},
            auth_failure=True,
        )
        token = _json(response, TokenResponse, exit_code=3)
        credentials.save_refresh(token.refresh_token)
        return token.access_token

    def create_job(
        self,
        manifest: ServerManifest,
        idempotency_key: str,
        access_token: str,
    ) -> JobResponse:
        response = self._request(
            "POST",
            "/api/v1/device/import-jobs",
            access_token=access_token,
            json_body=server_payload(manifest),
            headers={"Idempotency-Key": idempotency_key},
        )
        parsed = _json(response, JobResponse, exit_code=5)
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
        return parsed

    def part_url(
        self,
        job_id: UUID,
        attachment_id: UUID,
        part_number: int,
        access_token: str,
    ) -> str:
        response = self._request(
            "POST",
            f"/api/v1/device/import-jobs/{job_id}/attachments/{attachment_id}/parts/{part_number}",
            access_token=access_token,
        )
        parsed = _json(response, PartUrlResponse, exit_code=5)
        return parsed.url

    def put_part(self, url: str, content: bytes) -> str:
        try:
            response = self._client.put(url, content=content)
        except httpx.RequestError as error:
            raise ConnectorError(6, TEMPORARY_FAILURE) from error
        if response.status_code == 429 or response.status_code >= 500:
            raise ConnectorError(6, TEMPORARY_FAILURE)
        if not 200 <= response.status_code < 300:
            raise ConnectorError(5, SERVER_REJECTED)
        raw_etag = response.headers.get("ETag")
        etag = "" if raw_etag is None else str(raw_etag).strip()
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
        response = self._request(
            "POST",
            f"/api/v1/device/import-jobs/{job_id}/attachments/{expected.id}/complete",
            access_token=access_token,
            json_body={"parts": parts},
        )
        parsed = _json(response, AttachmentResponse, exit_code=5)
        if (
            parsed.id != expected.id
            or parsed.file_id != expected.file_id
            or parsed.upload_id != expected.upload_id
            or parsed.kind != expected.kind
        ):
            raise ConnectorError(5, SERVER_REJECTED)
        return parsed

    def submit_job(self, job_id: UUID, access_token: str) -> SubmitResponse:
        response = self._request(
            "POST",
            f"/api/v1/device/import-jobs/{job_id}/submit",
            access_token=access_token,
        )
        parsed = _json(response, SubmitResponse, exit_code=5)
        if parsed.id != job_id:
            raise ConnectorError(5, SERVER_REJECTED)
        return parsed

    def status(self, job_id: UUID, access_token: str) -> JobResponse:
        response = self._request(
            "GET",
            f"/api/v1/device/import-jobs/{job_id}",
            access_token=access_token,
        )
        parsed = _json(response, JobResponse, exit_code=5)
        if parsed.id != job_id:
            raise ConnectorError(5, SERVER_REJECTED)
        return parsed
