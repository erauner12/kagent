from __future__ import annotations

import asyncio
import json

import pytest

from a2a_codex_sandbox.config import MissingCredentialsError
from a2a_codex_sandbox.state import OperationLedger, StateStore


async def collect(bridge, operation: str, prompt: str = "hello"):
    return [
        event
        async for event in bridge.run(
            operation_id=operation,
            message_id=f"message-{operation}",
            prompt=prompt,
        )
    ]


def test_identity_mapping_rejects_a_second_context(bridge_factory, tmp_path):
    data = tmp_path / "actor"
    first = bridge_factory(context_id="context-1", data=data)
    with pytest.raises(ValueError, match="different A2A context"):
        bridge_factory(context_id="context-2", data=data)
    assert StateStore(first.paths, "context-1").state.logical_session_id == first.store.state.logical_session_id


@pytest.mark.asyncio
async def test_busy_rejection_does_not_start_second_prompt(bridge_factory):
    bridge = bridge_factory("hang_prompt")
    active = bridge.run(operation_id="operation-1", message_id="message-1", prompt="cancel")
    await anext(active)

    rejected = await collect(bridge, "operation-2")
    assert rejected[-1].outcome == "rejected"
    assert rejected[-1].stop_reason == "busy"
    methods = [
        json.loads(line)["method"]
        for line in (bridge.paths.bridge / "fake-trace.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert methods.count("session/prompt") == 1
    await active.aclose()
    await bridge.close()


@pytest.mark.asyncio
async def test_completed_duplicate_is_idempotent(bridge_factory):
    bridge = bridge_factory()
    first = await collect(bridge, "operation-1")
    duplicate = await collect(bridge, "operation-1")
    assert duplicate[-1].duplicate
    assert duplicate[-1].text == first[-1].text
    methods = [
        json.loads(line)["method"]
        for line in (bridge.paths.bridge / "fake-trace.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert methods.count("session/prompt") == 1
    await bridge.close()


def test_terminal_settlement_is_unique_and_generation_guarded(bridge_factory):
    bridge = bridge_factory()
    ledger = OperationLedger(bridge.paths)
    ledger.start("operation-1", "message-1", 1)
    first = ledger.settle("operation-1", 1, outcome="completed", output="first")
    second = ledger.settle("operation-1", 1, outcome="failed", output="second")
    stale = ledger.settle("operation-1", 0, outcome="failed")
    assert first is not None and first.output == "first"
    assert second is None
    assert stale is None
    assert ledger.get("operation-1").output == "first"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scenario", "returncode"),
    [("clean_exit", 0), ("crash", 3)],
)
async def test_child_clean_and_failed_exit_settle_failure(bridge_factory, scenario, returncode):
    bridge = bridge_factory(scenario)
    events = await collect(bridge, "operation-1")
    assert events[-1].outcome == "failed"
    assert bridge.ledger.get("operation-1").error == "ChildExitedError"
    assert any(
        event["kind"] == "child_exit" and event["returncode"] == returncode for event in bridge.diagnostics.events
    )


@pytest.mark.asyncio
async def test_stale_generation_cannot_teardown_or_settle_new_operation(bridge_factory):
    bridge = bridge_factory("hang_prompt")
    first = bridge.run(operation_id="operation-1", message_id="message-1", prompt="cancel")
    await anext(first)
    generation = bridge.generation
    await first.aclose()

    second = bridge.run(operation_id="operation-2", message_id="message-2", prompt="cancel")
    await anext(second)
    assert bridge.generation == generation + 1
    assert not await bridge.teardown_generation(generation)
    assert bridge.ledger.active().operation_id == "operation-2"
    assert bridge.ledger.settle("operation-2", generation, outcome="failed") is None
    await second.aclose()
    assert bridge.ledger.get("operation-2").outcome == "canceled"
    await bridge.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["hang_initialize", "hang_new"])
async def test_bootstrap_control_plane_timeouts(bridge_factory, scenario):
    bridge = bridge_factory(scenario, control_timeout=0.05)
    events = await collect(bridge, "operation-1")
    assert events[-1].outcome == "failed"
    assert bridge.ledger.get("operation-1").error == "TimeoutError"
    assert any(event["kind"] == "acp_timeout" for event in bridge.diagnostics.events)


@pytest.mark.asyncio
async def test_load_timeout_is_bounded(bridge_factory, tmp_path):
    data = tmp_path / "actor"
    first = bridge_factory(data=data)
    await collect(first, "operation-1")
    await first.close()

    resumed = bridge_factory("hang_load", data=data, control_timeout=0.05)
    events = await collect(resumed, "operation-2")
    assert events[-1].outcome == "failed"
    assert resumed.ledger.get("operation-2").error == "TimeoutError"


@pytest.mark.asyncio
async def test_missing_credentials_fails_prompt_not_readiness(bridge_factory, monkeypatch):
    monkeypatch.delenv("CODEX_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    bridge = bridge_factory(require_credentials=True)
    with pytest.raises(MissingCredentialsError):
        bridge.child.validate_credentials(bridge.paths)
    events = await collect(bridge, "operation-1")
    assert events[-1].outcome == "failed"
    assert bridge.ledger.get("operation-1").error == "MissingCredentialsError"


@pytest.mark.asyncio
async def test_failed_initialization_is_terminal(bridge_factory):
    bridge = bridge_factory("fail_initialize")
    events = await collect(bridge, "operation-1")
    assert len([event for event in events if event.kind == "terminal"]) == 1
    assert events[-1].outcome == "failed"
    assert bridge.ledger.get("operation-1").error == "ACPError"


@pytest.mark.asyncio
async def test_cancel_race_is_idempotent(bridge_factory):
    bridge = bridge_factory("hang_prompt")
    iterator = bridge.run(operation_id="operation-1", message_id="message-1", prompt="cancel")
    await anext(iterator)
    generation = bridge.generation
    assert await bridge.cancel(generation)
    assert not await bridge.cancel(generation - 1)
    await iterator.aclose()
    record = bridge.ledger.get("operation-1")
    assert record.terminal and record.outcome == "canceled"
    await bridge.close()
