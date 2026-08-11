"""Bounded local-password validation and Argon2id primitives."""

from __future__ import annotations

import secrets
import unicodedata
from dataclasses import dataclass

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

MIN_PASSWORD_CODEPOINTS = 12
MAX_PASSWORD_CODEPOINTS = 128
MAX_PASSWORD_UTF8_BYTES = 512

_HASHER = PasswordHasher(
    time_cost=2,
    memory_cost=19_456,
    parallelism=1,
    hash_len=32,
    salt_len=16,
    type=Type.ID,
)
_DUMMY_HASH = _HASHER.hash(secrets.token_urlsafe(32))


class PasswordPolicyError(ValueError):
    """Password cannot be accepted under the local credential policy."""

    def __init__(self) -> None:
        super().__init__("Password does not satisfy the credential policy")


@dataclass(frozen=True)
class PasswordVerification:
    """Safe password verification outcome."""

    valid: bool
    needs_rehash: bool


def validate_password(raw: str) -> None:
    """Validate an exact password without trimming or normalization."""
    if not isinstance(raw, str) or not (
        MIN_PASSWORD_CODEPOINTS <= len(raw) <= MAX_PASSWORD_CODEPOINTS
    ):
        raise PasswordPolicyError
    if any(unicodedata.category(character) == "Cc" for character in raw):
        raise PasswordPolicyError
    try:
        encoded = raw.encode("utf-8", errors="strict")
    except UnicodeEncodeError as error:
        raise PasswordPolicyError from error
    if len(encoded) > MAX_PASSWORD_UTF8_BYTES:
        raise PasswordPolicyError


def hash_password(raw: str) -> str:
    """Validate and encode a password with the pinned Argon2id policy."""
    validate_password(raw)
    return _HASHER.hash(raw)


def verify_password(encoded_hash: str, raw: str) -> PasswordVerification:
    """Verify a password without surfacing stored-hash parser details."""
    try:
        validate_password(raw)
        valid = _HASHER.verify(encoded_hash, raw)
    except (PasswordPolicyError, InvalidHashError, VerificationError, VerifyMismatchError):
        return PasswordVerification(valid=False, needs_rehash=False)
    return PasswordVerification(
        valid=bool(valid),
        needs_rehash=bool(valid and _HASHER.check_needs_rehash(encoded_hash)),
    )


def verify_dummy_password(raw: str) -> None:
    """Spend the normal verification cost for an unknown local username."""
    verify_password(_DUMMY_HASH, raw)


def new_temporary_password() -> str:
    """Generate a high-entropy printable temporary password."""
    value = secrets.token_urlsafe(24)
    validate_password(value)
    return value
