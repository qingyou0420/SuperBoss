"""Local password policy and Argon2id contract."""

from __future__ import annotations

import importlib
import importlib.util
from types import ModuleType

import pytest


def _passwords() -> ModuleType:
    spec = importlib.util.find_spec("superboss.modules.auth.passwords")
    assert spec is not None, "local password module is required"
    return importlib.import_module("superboss.modules.auth.passwords")


@pytest.mark.parametrize(
    "password",
    [
        "too short",
        "x" * 129,
        "x" * 11 + "\x00",
        "x" * 11 + "\n",
        "x" * 11 + "\x7f",
        "x" * 11 + "\u0085",
        "x" * 11 + "\ud800",
    ],
)
def test_password_policy_rejects_invalid_lengths_controls_and_surrogates(
    password: str,
) -> None:
    passwords = _passwords()

    with pytest.raises(passwords.PasswordPolicyError) as caught:
        passwords.validate_password(password)

    assert password not in str(caught.value)


@pytest.mark.parametrize(
    "password",
    [
        "twelve chars",
        " correct horse battery staple ",
        "森林月光照亮今晚安静的回家路",
        "x" * 128,
        "😀" * 128,
    ],
)
def test_password_policy_accepts_exact_printable_passphrases(password: str) -> None:
    _passwords().validate_password(password)


def test_password_policy_does_not_trim_or_normalize() -> None:
    passwords = _passwords()
    raw = " cafe\u0301 password "
    normalized = " café password "

    encoded = passwords.hash_password(raw)

    assert passwords.verify_password(encoded, raw).valid is True
    assert passwords.verify_password(encoded, raw.strip()).valid is False
    assert passwords.verify_password(encoded, normalized).valid is False


def test_hash_uses_exact_argon2id_policy_and_random_salts() -> None:
    passwords = _passwords()

    first = passwords.hash_password("correct horse battery staple")
    second = passwords.hash_password("correct horse battery staple")

    assert first.startswith("$argon2id$v=19$m=19456,t=2,p=1$")
    assert second.startswith("$argon2id$v=19$m=19456,t=2,p=1$")
    assert first != second
    assert len(first) <= 255


def test_verify_is_bounded_and_safe_for_wrong_or_malformed_hashes() -> None:
    passwords = _passwords()
    encoded = passwords.hash_password("correct horse battery staple")

    wrong = passwords.verify_password(encoded, "wrong password phrase")
    malformed = passwords.verify_password("not-an-argon2-hash", "wrong password phrase")

    assert wrong.valid is False and wrong.needs_rehash is False
    assert malformed.valid is False and malformed.needs_rehash is False


def test_verify_reports_rehash_only_after_valid_legacy_hash() -> None:
    passwords = _passwords()
    argon2 = importlib.import_module("argon2")
    legacy = argon2.PasswordHasher(
        time_cost=1,
        memory_cost=8192,
        parallelism=1,
        hash_len=32,
        salt_len=16,
    ).hash("correct horse battery staple")

    valid = passwords.verify_password(legacy, "correct horse battery staple")
    wrong = passwords.verify_password(legacy, "wrong password phrase")

    assert valid.valid is True and valid.needs_rehash is True
    assert wrong.valid is False and wrong.needs_rehash is False


def test_generated_temporary_password_has_high_entropy_safe_shape() -> None:
    passwords = _passwords()

    generated = {passwords.new_temporary_password() for _ in range(64)}

    assert len(generated) == 64
    assert all(24 <= len(value) <= 64 for value in generated)
    assert all(value.isascii() and value.isprintable() for value in generated)
    assert all(" " not in value for value in generated)
    for value in generated:
        passwords.validate_password(value)
