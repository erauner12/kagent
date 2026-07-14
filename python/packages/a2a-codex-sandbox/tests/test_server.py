from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx
import pytest

from a2a_codex_sandbox.config import ChildConfig, RuntimePaths
from a2a_codex_sandbox.server import A2AAdapter, A2ARequest, ServerConfig, create_app


def child_config(paths: RuntimePaths, scenario: str = "normal") -> ChildConfig:
    source = Path(__file__).parents[1] / "src"
    pythonpath = str(source)
    if os.getenv("PYTHONPATH"):
        pythonpath += os.pathsep + os.environ["PYTHONPATH"]
    return ChildConfig(
        command=(sys.executable, "-m", "a2a_codex_sandbox.fake_acp"),
        extra_env={
            "FAKE_ACP_SCENARIO": scenario,
            "FAKE_ACP_TRACE": str(paths.bridge / "fake-trace.jsonl"),
            "PYTHONPATH": pythonpath,
        },
        require_credentials=False,
    )


def rpc_request(
    method: str,
    *,
    request_id: str = "request-1",
    context_id: str = "context-1",
    message_id: str = "message-1",
    task_id: str | None = "task-1",
    prompt: str = "hello",
) -> dict:
    message = {
        "kind": "message",
        "role": "user",
        "messageId": message_id,
        "contextId": context_id,
        "parts": [{"kind": "text", "text": prompt}],
    }
    if task_id is not None:
        message["taskId"] = task_id
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": {"message": message},
    }


def sse_results(response: httpx.Response) -> list[dict]:
    return [
        json.loads(line.removeprefix("data: "))["result"]
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


def test_server_defaults_match_byo_runtime_port(monkeypatch):
    monkeypatch.delenv("A2A_HOST", raising=False)
    monkeypatch.delenv("A2A_PORT", raising=False)
    config = ServerConfig.from_env()
    assert config.host == "0.0.0.0"
    assert config.port == 80


@pytest.mark.asyncio
async def test_agent_card_readiness_is_provider_independent(tmp_path, monkeypatch):
    monkeypatch.delenv("CODEX_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    paths = RuntimePaths.create(tmp_path)
    child = child_config(paths)
    app = create_app(
        paths=paths,
        child=child,
        config=ServerConfig(agent_url="http://sandbox"),
    )

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/.well-known/agent-card.json")

    assert response.status_code == 200
    card = response.json()
    assert card["url"] == "http://sandbox"
    assert card["capabilities"]["streaming"] is True


@pytest.mark.asyncio
async def test_agent_card_requires_child_executable(tmp_path):
    paths = RuntimePaths.create(tmp_path)
    child = ChildConfig(
        command=(str(tmp_path / "missing-child"),),
        extra_env={},
        require_credentials=True,
    )
    app = create_app(paths=paths, child=child)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/.well-known/agent-card.json")

    assert response.status_code == 503
    assert response.json()["detail"] == "ACP child executable is not available"


@pytest.mark.asyncio
async def test_message_send_maps_ids_prompt_and_terminal_task(bridge_factory):
    bridge = bridge_factory()
    app = create_app(
        paths=bridge.paths,
        child=bridge.child,
        bridge_factory=lambda context_id: bridge,
    )

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/",
            json=rpc_request(
                "message/send",
                context_id="context-1",
                message_id="message-7",
                task_id="task-7",
                prompt="workspace",
            ),
        )

    assert response.status_code == 200
    task = response.json()["result"]
    assert task["id"] == "task-7"
    assert task["contextId"] == "context-1"
    assert task["status"]["state"] == "completed"
    record = bridge.ledger.get("task-7")
    assert record is not None and record.message_id == "message-7"
    trace = [
        json.loads(line) for line in (bridge.paths.bridge / "fake-trace.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert any(entry["method"] == "session/prompt" for entry in trace)
    assert (bridge.paths.workspace / "marker.txt").read_text(encoding="utf-8") == "workspace-ok"


@pytest.mark.asyncio
async def test_message_id_is_operation_id_when_task_id_is_absent(bridge_factory):
    bridge = bridge_factory()
    app = create_app(
        paths=bridge.paths,
        child=bridge.child,
        bridge_factory=lambda context_id: bridge,
    )

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/",
            json=rpc_request("message/send", message_id="message-only", task_id=None),
        )

    assert response.json()["result"]["id"] == "message-only"
    assert bridge.ledger.get("message-only") is not None


@pytest.mark.asyncio
async def test_message_stream_emits_one_terminal_result_and_closes(bridge_factory):
    bridge = bridge_factory()
    app = create_app(
        paths=bridge.paths,
        child=bridge.child,
        bridge_factory=lambda context_id: bridge,
    )

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/", json=rpc_request("message/stream"))

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    results = sse_results(response)
    terminals = [result for result in results if result.get("kind") == "status-update" and result.get("final")]
    assert len(terminals) == 1
    assert terminals[0]["status"]["state"] == "completed"
    assert results[-1] == terminals[0]


@pytest.mark.asyncio
async def test_duplicate_delivery_returns_one_duplicate_terminal(bridge_factory):
    bridge = bridge_factory()
    app = create_app(
        paths=bridge.paths,
        child=bridge.child,
        bridge_factory=lambda context_id: bridge,
    )

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/", json=rpc_request("message/send"))
        response = await client.post("/", json=rpc_request("message/stream"))

    results = sse_results(response)
    assert len(results) == 1
    assert results[0]["final"] is True
    assert results[0]["metadata"]["duplicate"] is True
    assert results[0]["status"]["state"] == "completed"


@pytest.mark.asyncio
async def test_busy_is_terminal_rejected_task_not_http_error(bridge_factory):
    bridge = bridge_factory("hang_prompt")
    active = bridge.run(operation_id="active", message_id="active-message", prompt="cancel")
    await anext(active)
    app = create_app(
        paths=bridge.paths,
        child=bridge.child,
        bridge_factory=lambda context_id: bridge,
    )

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/",
            json=rpc_request("message/send", message_id="busy-message", task_id="busy-task"),
        )

    assert response.status_code == 200
    task = response.json()["result"]
    assert task["status"]["state"] == "rejected"
    assert task["metadata"]["stopReason"] == "busy"
    await active.aclose()


@pytest.mark.asyncio
async def test_adapter_generator_close_cancels_bridge(bridge_factory):
    bridge = bridge_factory("hang_prompt")
    adapter = A2AAdapter(lambda context_id: bridge)
    stream = adapter.stream(
        A2ARequest(
            context_id="context-1",
            message_id="message-cancel",
            operation_id="task-cancel",
            task_id="task-cancel",
            prompt="cancel",
        )
    )

    await anext(stream)
    await stream.aclose()

    record = bridge.ledger.get("task-cancel")
    assert record is not None
    assert record.terminal
    assert record.outcome == "canceled"


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["tasks/cancel", "tasks/get", "tasks/resubscribe"])
async def test_task_id_only_control_methods_are_clearly_unsupported(tmp_path, method):
    paths = RuntimePaths.create(tmp_path)
    app = create_app(paths=paths, child=child_config(paths))

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/",
            json={"jsonrpc": "2.0", "id": "request-1", "method": method, "params": {"id": "task-1"}},
        )

    error = response.json()["error"]
    assert error["code"] == -32601
    assert error["message"] == f"Method not supported: {method}"
