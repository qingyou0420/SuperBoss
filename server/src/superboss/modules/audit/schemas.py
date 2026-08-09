"""Validated, safe audit event inputs."""

import math
from uuid import UUID

from pydantic import BaseModel, field_validator

from superboss.core.actors import Actor

_REDACTED = "[REDACTED]"
_FORBIDDEN_METADATA_KEYS = frozenset(
    {"access_token", "refresh_token", "authorization", "cookie", "file_content", "model_input"}
)


def sanitize_metadata(value: object) -> dict[str, object]:
    """Return structured JSON while replacing credential-bearing values at every nesting level."""
    if not isinstance(value, dict):
        raise TypeError("metadata must be an object")
    sanitized = _sanitize_value(value)
    assert isinstance(sanitized, dict)
    return sanitized


def _sanitize_value(value: object) -> object:
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("metadata numbers must be finite")
        return value
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, dict):
        sanitized: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("metadata keys must be strings")
            sanitized[key] = _REDACTED if key.lower() in _FORBIDDEN_METADATA_KEYS else _sanitize_value(item)
        return sanitized
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
        return sanitize_metadata(value)
