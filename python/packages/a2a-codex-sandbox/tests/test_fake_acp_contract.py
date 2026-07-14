from __future__ import annotations

import json
from pathlib import Path

import pytest


async def collect(bridge, operation: str, prompt: str):
    return [
        event
        async for event in bridge.run(
            operation_id=operation,
            message_id=f"message-{operation}",
            prompt=prompt,
        )
    ]


def trace_methods(path: Path) -> list[str]:
    return [json.loads(line)["method"] for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.mark.asyncio
async def test_initialize_new_prompt_streaming_and_sequencing(bridge_factory):
    bridge = bridge_factory()
    events = await collect(bridge, "operation-1", "hello")
    assert [event.text for event in events if event.kind == "update"] == ["hello ", "world"]
    assert events[-1].outcome == "completed"
    assert events[-1].stop_reason == "end_turn"
    assert bridge.store.state.acp_session_id == "fake-session-1"
    assert bridge.store.state.codex_thread_id == "fake-session-1"
    assert bridge.store.state.session_id_confidence == "candidate"
    assert trace_methods(bridge.paths.bridge / "fake-trace.jsonl")[:3] == [
        "initialize",
        "session/new",
        "session/prompt",
    ]
    await bridge.close()


@pytest.mark.asyncio
async def test_load_success_suppresses_replay(bridge_factory, tmp_path):
    data = tmp_path / "actor"
    first = bridge_factory(data=data)
    await collect(first, "operation-1", "first")
    await first.close()

    resumed = bridge_factory(data=data)
    events = await collect(resumed, "operation-2", "second")
    text = "".join(event.text for event in events)
    assert "historical replay" not in text
    assert resumed.store.state.session_id_confidence == "verified"
    assert any(event["kind"] == "load_replay_suppressed" for event in resumed.diagnostics.events)
    await resumed.close()


@pytest.mark.asyncio
async def test_load_not_found_falls_back_only_for_pinned_shape(bridge_factory, tmp_path):
    data = tmp_path / "actor"
    first = bridge_factory(data=data)
    await collect(first, "operation-1", "first")
    prior_id = first.store.state.acp_session_id
    await first.close()

    resumed = bridge_factory("load_not_found", data=data)
    events = await collect(resumed, "operation-2", "second")
    assert events[-1].outcome == "completed"
    assert prior_id in resumed.store.state.prior_acp_session_ids
    assert prior_id in resumed.store.state.prior_codex_thread_ids
    assert resumed.store.state.session_id_confidence == "candidate"
    methods = trace_methods(resumed.paths.bridge / "fake-trace.jsonl")
    assert methods[-4:-1] == ["initialize", "session/load", "session/new"]
    assert any(event["kind"] == "load_fallback" for event in resumed.diagnostics.events)
    await resumed.close()


@pytest.mark.asyncio
async def test_arbitrary_load_error_does_not_fallback(bridge_factory, tmp_path):
    data = tmp_path / "actor"
    first = bridge_factory(data=data)
    await collect(first, "operation-1", "first")
    await first.close()

    resumed = bridge_factory("load_auth_error", data=data)
    events = await collect(resumed, "operation-2", "second")
    assert events[-1].outcome == "failed"
    methods = trace_methods(resumed.paths.bridge / "fake-trace.jsonl")
    assert methods[-2:] == ["initialize", "session/load"]
    assert not any(event["kind"] == "load_fallback" for event in resumed.diagnostics.events)


@pytest.mark.asyncio
async def test_disconnect_sends_cancel_and_settles(bridge_factory):
    bridge = bridge_factory("hang_prompt")
    iterator = bridge.run(operation_id="operation-1", message_id="message-1", prompt="cancel")
    first = await anext(iterator)
    assert first.kind == "update"
    await iterator.aclose()

    record = bridge.ledger.get("operation-1")
    assert record is not None and record.terminal
    assert record.outcome == "canceled"
    assert trace_methods(bridge.paths.bridge / "fake-trace.jsonl")[-1] == "session/cancel"
    assert not bridge.paths.active_operation_file.exists()
    await bridge.close()


@pytest.mark.asyncio
async def test_controlled_failure_has_one_terminal_record(bridge_factory):
    bridge = bridge_factory("controlled_failure")
    events = await collect(bridge, "operation-1", "fail")
    assert len([event for event in events if event.kind == "terminal"]) == 1
    assert events[-1].outcome == "failed"
    assert bridge.ledger.get("operation-1").error == "ACPError"


@pytest.mark.asyncio
async def test_diagnostics_are_redacted_and_capture_protocol_faults(bridge_factory):
    bridge = bridge_factory("diagnostics")
    events = await collect(bridge, "operation-1", "hello")
    assert events[-1].outcome == "completed"
    kinds = {event["kind"] for event in bridge.diagnostics.events}
    assert {"child_stderr", "invalid_json", "unmatched_response", "acp_jsonrpc"} <= kinds
    diagnostics = bridge.paths.diagnostics_file.read_text(encoding="utf-8")
    assert "super-secret-token" not in diagnostics
    assert "[REDACTED]" in diagnostics
    assert "hello" not in diagnostics
    await bridge.close()


@pytest.mark.asyncio
async def test_workspace_marker_read_write_via_acp_client_methods(bridge_factory):
    bridge = bridge_factory()
    events = await collect(bridge, "operation-1", "workspace")
    assert bridge.paths.workspace.joinpath("marker.txt").read_text(encoding="utf-8") == "workspace-ok"
    assert "workspace-ok" in "".join(event.text for event in events)
    await bridge.close()


@pytest.mark.asyncio
async def test_fake_e2e_marker_and_session_evidence(bridge_factory):
    bridge = bridge_factory(context_id="context-e2e")
    written = await collect(bridge, "operation-write", "marker-write:actor-a")
    assert "marker:actor-a" in "".join(event.text for event in written)

    read = await collect(bridge, "operation-read", "marker-read")
    assert "marker:actor-a" in "".join(event.text for event in read)

    evidence = await collect(bridge, "operation-evidence", "evidence")
    payload = "".join(event.text for event in evidence)
    assert '"acpSessionId": "fake-session-1"' in payload
    assert f'"workspace": "{bridge.paths.workspace}"' in payload
    await bridge.close()


@pytest.mark.asyncio
async def test_permission_and_elicitation_are_denied_visibly(bridge_factory):
    bridge = bridge_factory()
    events = await collect(bridge, "operation-1", "permission elicitation")
    visible = [event.text for event in events if event.kind == "update"]
    assert visible.count("Permission request denied by bridge policy.") == 2
    trace = (bridge.paths.bridge / "fake-trace.jsonl").read_text(encoding="utf-8")
    assert '"outcome": "cancelled"' in trace
    assert '"action": "cancel"' in trace
    await bridge.close()
