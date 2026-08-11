"""Token and browser-CSRF primitives."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt

from superboss.core.config import Settings

DEVICE_ACCESS_SCOPES = (
    "imports:create",
    "imports:read-own",
    "imports:submit",
    "imports:upload",
)
_DEVICE_ACCESS_CLAIMS = {
    "sub",
    "device_id",
    "owner_id",
    "scopes",
    "session_id",
    "iat",
    "exp",
    "jti",
}


class TokenError(Exception):
    """Credential was malformed, expired, or unsuitable for this operation."""


def utcnow() -> datetime:
    return datetime.now(UTC)


def hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def new_opaque_token() -> str:
    return secrets.token_urlsafe(48)


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def issue_access_token(
    settings: Settings, user_id: UUID, role: str, session_id: UUID
) -> tuple[str, datetime]:
    if not settings.jwt_secret:
        raise TokenError("JWT signing is not configured")
    issued_at = utcnow().replace(microsecond=0)
    expires_at = issued_at + timedelta(hours=2)
    payload = {
        "sub": str(user_id),
        "role": role,
        "session_id": str(session_id),
        "iat": issued_at,
        "exp": expires_at,
        "jti": str(uuid4()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256"), expires_at


def decode_access_token(settings: Settings, token: str) -> dict[str, object]:
    if not settings.jwt_secret:
        raise TokenError("JWT signing is not configured")
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError as error:
        raise TokenError("Invalid access token") from error
    if set(payload) != {"sub", "role", "session_id", "iat", "exp", "jti"}:
        raise TokenError("Invalid access token claims")
    if not all(
        isinstance(payload[key], str) and payload[key]
        for key in ("sub", "role", "session_id", "jti")
    ):
        raise TokenError("Invalid access token claims")
    if not all(type(payload[key]) is int for key in ("iat", "exp")):
        raise TokenError("Invalid access token claims")
    return payload


def issue_device_access_token(
    settings: Settings,
    *,
    device_id: UUID,
    owner_id: UUID,
    session_id: UUID,
    access_jti: UUID,
    issued_at: datetime,
) -> tuple[str, datetime]:
    """Issue a device JWT that cannot be decoded as a browser session."""
    if not settings.jwt_secret:
        raise TokenError("JWT signing is not configured")
    expires_at = issued_at + timedelta(hours=2)
    payload = {
        "sub": str(device_id),
        "device_id": str(device_id),
        "owner_id": str(owner_id),
        "scopes": list(DEVICE_ACCESS_SCOPES),
        "session_id": str(session_id),
        "iat": issued_at,
        "exp": expires_at,
        "jti": str(access_jti),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256"), expires_at


def decode_device_access_token(settings: Settings, token: str) -> dict[str, object]:
    """Decode only the exact, least-privilege device access-token shape."""
    if not settings.jwt_secret:
        raise TokenError("JWT signing is not configured")
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=["HS256"],
            options={"verify_exp": False, "verify_iat": False},
        )
    except jwt.PyJWTError as error:
        raise TokenError("Invalid device access token") from error
    if set(payload) != _DEVICE_ACCESS_CLAIMS:
        raise TokenError("Invalid device access token claims")
    if not all(type(payload[key]) is int for key in ("iat", "exp")):
        raise TokenError("Invalid device access token claims")
    if payload["exp"] != payload["iat"] + 2 * 60 * 60:
        raise TokenError("Invalid device access token claims")
    if payload["scopes"] != list(DEVICE_ACCESS_SCOPES):
        raise TokenError("Invalid device access token claims")
    try:
        subject_id = UUID(str(payload["sub"]))
        device_id = UUID(str(payload["device_id"]))
        UUID(str(payload["owner_id"]))
        UUID(str(payload["session_id"]))
        UUID(str(payload["jti"]))
    except (TypeError, ValueError) as error:
        raise TokenError("Invalid device access token claims") from error
    if subject_id != device_id:
        raise TokenError("Invalid device access token claims")
    return payload
