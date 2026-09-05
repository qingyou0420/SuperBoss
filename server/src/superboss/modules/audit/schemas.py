"""Validated audit inputs with bounded, Unicode-aware secret redaction."""

import json
import math
import unicodedata
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator

from superboss.core.actors import Actor

_REDACTED = "[REDACTED]"
_MAX_DEPTH = 32
_MAX_NODES = 1000
_MAX_TEXT_BYTES = 64 * 1024
_MAX_JSON_INTEGER = 2**53 - 1
_FORBIDDEN_METADATA_KEYS = frozenset(
    unicodedata.normalize("NFKC", key).casefold()
    for key in (
        "access_token",
        "refresh_token",
        "authorization",
        "cookie",
        "file_content",
        "model_input",
    )
)


def sanitize_metadata(value: object) -> dict[str, object]:
    """Copy structured JSON, redacting normalized secret keys within documented resource limits.

    Containers are limited to 32 nesting levels and 1,000 nodes; UTF-8 text (keys and
    values) is limited to 64 KiB. Cycles are rejected instead of recursing indefinitely.
    """
    if not isinstance(value, dict):
        raise TypeError("metadata must be an object")
    budget = _Budget()
    _validate_value(value, 0, set(), budget)
    result = _copy_sanitized(value)
    assert isinstance(result, dict)
    encoded = json.dumps(result, allow_nan=False, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    if len(encoded) > _MAX_TEXT_BYTES:
        raise ValueError("metadata exceeds JSON size budget")
    return result


class _Budget:
    def __init__(self) -> None:
        self.nodes = 0
        self.text_bytes = 0

    def node(self) -> None:
        self.nodes += 1
        if self.nodes > _MAX_NODES:
            raise ValueError("metadata exceeds node budget")

    def text(self, value: str) -> None:
        self.text_bytes += len(value.encode("utf-8"))
        if self.text_bytes > _MAX_TEXT_BYTES:
            raise ValueError("metadata exceeds text budget")


def _validate_value(value: object, depth: int, ancestors: set[int], budget: _Budget) -> None:
    budget.node()
    if depth > _MAX_DEPTH:
        raise ValueError("metadata exceeds maximum depth")
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, str):
        budget.text(value)
        return
    if isinstance(value, int):
        if value < -_MAX_JSON_INTEGER or value > _MAX_JSON_INTEGER:
            raise ValueError("metadata integer is outside JSON interoperability range")
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("metadata numbers must be finite")
        return
    if isinstance(value, (dict, list)):
        identity = id(value)
        if identity in ancestors:
            raise ValueError("metadata must not contain cycles")
        ancestors.add(identity)
        try:
            if isinstance(value, list):
                for item in value:
                    _validate_value(item, depth + 1, ancestors, budget)
                return
            for key, item in value.items():
                if not isinstance(key, str):
                    raise TypeError("metadata keys must be strings")
                budget.text(key)
                _validate_value(item, depth + 1, ancestors, budget)
            return
        finally:
            ancestors.remove(identity)
    raise ValueError("metadata must contain JSON values only")


def _copy_sanitized(value: object) -> object:
    """Copy already validated metadata; sensitive subtrees are never copied."""
    if isinstance(value, list):
        return [_copy_sanitized(item) for item in value]
    if isinstance(value, dict):
        return {
            key: (
                _REDACTED
                if unicodedata.normalize("NFKC", key).casefold() in _FORBIDDEN_METADATA_KEYS
                else _copy_sanitized(item)
            )
            for key, item in value.items()
        }
    return value


class AuditRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    actor_kind: str
    actor_id: UUID | None
    action: str
    object_type: str
    object_id: UUID | None
    project_id: UUID | None
    outcome: str
    metadata_json: dict[str, object]
    request_id: UUID | None
    created_at: datetime


class AuditEventInput(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)
    actor: Actor
    action: str
    object_type: str
    object_id: UUID | None = None
    project_id: UUID | None = None
    outcome: str
    request_id: UUID
    event_key: UUID | None = None
    metadata: dict[str, object] = {}

    @field_validator("metadata", mode="before")
    @classmethod
    def validate_metadata(cls, value: object) -> dict[str, object]:
        try:
            return sanitize_metadata(value)
        except (TypeError, ValueError) as error:
            raise ValueError("invalid audit metadata") from error
