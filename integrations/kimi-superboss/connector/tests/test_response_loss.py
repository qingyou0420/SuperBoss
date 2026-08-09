from __future__ import annotations

from pathlib import Path

import pytest
import respx
from conftest import (
    ORIGIN,
    TIMESTAMP,
    MemoryKeyring,
    install_response_loss_flow,
    load_app,
)
from typer.testing import CliRunner


@pytest.mark.parametrize(
    ("loss_stage", "first_counts", "final_counts"),
    [
        ("create", (1, 0, 0, 0), (2, 1, 1, 1)),
        ("complete", (1, 1, 1, 0), (1, 1, 2, 1)),
        ("submit", (1, 1, 1, 1), (1, 1, 1, 2)),
    ],
)
def test_accepted_response_loss_replays_only_the_idempotent_stage(
    loss_stage: str,
    first_counts: tuple[int, int, int, int],
    final_counts: tuple[int, int, int, int],
    tmp_path: Path,
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    flow = install_response_loss_flow(
        tmp_path / loss_stage,
        memory_keyring,
        respx_mock,
        loss_stage=loss_stage,
    )

    first = runner.invoke(
        app,
        ["submit", "--server", ORIGIN, "--manifest", str(flow.manifest)],
    )

    assert first.exit_code == 6
    assert (
        flow.create_route.call_count,
        flow.put_route.call_count,
        flow.complete_route.call_count,
        flow.submit_route.call_count,
    ) == first_counts
    entries = list(state_dir.rglob("*.json"))
    assert len(entries) == 1
    persisted = entries[0].read_text(encoding="utf-8").lower()
    assert flow.idempotency_key in persisted
    for secret in (
        "access-1",
        "refresh-0",
        "refresh-1",
        "authorization",
        "storage.local",
    ):
        assert secret not in persisted

    retried = runner.invoke(app, ["retry", "--server", ORIGIN])

    assert retried.exit_code == 0
    assert flow.refresh_route.call_count == 2
    assert (
        flow.create_route.call_count,
        flow.put_route.call_count,
        flow.complete_route.call_count,
        flow.submit_route.call_count,
    ) == final_counts
    assert flow.part_route.call_count == 1
    assert flow.accepted == {
        "child_allocations": 1,
        "completions": 1,
        "submissions": 1,
    }
    assert {call.request.headers["Idempotency-Key"] for call in flow.create_route.calls} == {
        flow.idempotency_key
    }
    for call in flow.create_route.calls:
        assert f'"processed_at":"{TIMESTAMP}"' in call.request.read().decode("utf-8")
    assert not list(state_dir.rglob("*.json"))
    combined = f"{first.stdout}\n{first.stderr}\n{retried.stdout}\n{retried.stderr}"
    for secret in (
        "access-1",
        "access-2",
        "refresh-0",
        "refresh-1",
        "refresh-2",
        "stable-etag",
        "storage.local",
    ):
        assert secret not in combined


def test_local_offset_timestamp_is_sent_as_utc_and_utc_echo_is_accepted(
    tmp_path: Path,
    runner: CliRunner,
    memory_keyring: MemoryKeyring,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    app = load_app(monkeypatch, state_dir)
    flow = install_response_loss_flow(
        tmp_path / "utc-normalization",
        memory_keyring,
        respx_mock,
        loss_stage="create",
    )

    first = runner.invoke(
        app,
        ["submit", "--server", ORIGIN, "--manifest", str(flow.manifest)],
    )
    retried = runner.invoke(app, ["retry", "--server", ORIGIN])

    assert first.exit_code == 6
    assert retried.exit_code == 0
    for call in flow.create_route.calls:
        assert f'"processed_at":"{TIMESTAMP}"' in call.request.read().decode("utf-8")
