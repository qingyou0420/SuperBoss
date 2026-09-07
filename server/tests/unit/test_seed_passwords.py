"""Acceptance seed can read both passwords from the environment without a TTY."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SEED_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "seed_acceptance.py"
OWNER_PASSWORD = "acceptance owner local password"
STAFF_PASSWORD = "acceptance staff local password"


def _module():
    spec = importlib.util.spec_from_file_location("seed_acceptance", SEED_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_read_passwords_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    monkeypatch.setenv("SUPERBOSS_OWNER_PASSWORD", OWNER_PASSWORD)
    monkeypatch.setenv("SUPERBOSS_ACCEPTANCE_STAFF_PASSWORD", STAFF_PASSWORD)

    def forbidden(_prompt: str) -> str:
        raise AssertionError("environment passwords must not prompt")

    owner, staff = await module._read_passwords(forbidden)
    assert owner == OWNER_PASSWORD and staff == STAFF_PASSWORD


@pytest.mark.asyncio
async def test_read_passwords_rejects_a_partial_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    monkeypatch.setenv("SUPERBOSS_OWNER_PASSWORD", OWNER_PASSWORD)
    monkeypatch.delenv("SUPERBOSS_ACCEPTANCE_STAFF_PASSWORD", raising=False)
    with pytest.raises(module.SeedRefusedError):
        await module._read_passwords(lambda _prompt: OWNER_PASSWORD)
