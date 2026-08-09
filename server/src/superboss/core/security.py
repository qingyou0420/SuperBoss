"""Token and browser-CSRF primitives."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt

from superboss.core.config import Settings


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
    issued_at = utcnow()
    expires_at = issued_at + timedelta(minutes=15)
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
