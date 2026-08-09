"""Validated audit inputs with bounded, Unicode-aware secret redaction."""

import math
import unicodedata
from uuid import UUID

from pydantic import BaseModel, field_validator

from superboss.core.actors import Actor

_REDACTED = "[REDACTED]"
_MAX_DEPTH = 32
_MAX_NODES = 1000
_MAX_TEXT_BYTES = 64 * 1024
_FORBIDDEN_METADATA_KEYS = frozenset(
    unicodedata.normalize("NFKC", key).casefold()
    for key in ("access_token", "refresh_token", "authorization", "cookie", "file_content", "model_input")
)


def sanitize_metadata(value: object) -> dict[str, object]:
    """Copy structured JSON, redacting normalized secret keys within documented resource limits.

    Containers are limited to 32 nesting levels and 1,000 nodes; UTF-8 text (keys and
    values) is limited to 64 KiB. Cycles are rejected instead of recursing indefinitely.
    """
    if not isinstance(value, dict):
        raise TypeError("metadata must be an object")
    budget = _Budget()
    result = _sanitize_value(value, 0, set(), budget)
    assert isinstance(result, dict)
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


def _sanitize_value(value: object, depth: int, ancestors: set[int], budget: _Budget) -> object:
    budget.node()
    if depth > _MAX_DEPTH:
        raise ValueError("metadata exceeds maximum depth")
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        budget.text(value)
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("metadata numbers must be finite")
        return value
    if isinstance(value, (dict, list)):
        identity = id(value)
        if identity in ancestors:
            raise ValueError("metadata must not contain cycles")
        ancestors.add(identity)
        try:
            if isinstance(value, list):
                return [_sanitize_value(item, depth + 1, ancestors, budget) for item in value]
            sanitized: dict[str, object] = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    raise TypeError("metadata keys must be strings")
                budget.text(key)
                normalized_key = unicodedata.normalize("NFKC", key).casefold()
                sanitized[key] = (
                    _REDACTED
                    if normalized_key in _FORBIDDEN_METADATA_KEYS
                    else _sanitize_value(item, depth + 1, ancestors, budget)
                )
            return sanitized
        finally:
            ancestors.remove(identity)
    raise ValueError("metadata must contain JSON values only")


class AuditEventInput(BaseModel):
    actor: Actor
    action: str
    object_type: str
    object_id: UUID | None = None
    project_id: UUID | None = None
    outcome: str
    request_id: UUID
    metadata: dict[str, object] = {}

    @field_validator("metadata", mode="before")
    @classmethod
    def validate_metadata(cls, value: object) -> dict[str, object]:
        try:
            return sanitize_metadata(value)
        except (TypeError, ValueError) as error:
            raise ValueError("invalid audit metadata") from error
