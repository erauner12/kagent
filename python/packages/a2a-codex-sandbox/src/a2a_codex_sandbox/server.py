"""Thin A2A HTTP adapter for the actor-local ACP bridge."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import uvicorn
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
    Artifact,
    Message,
    Part,
    Role,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    TextPart,
)
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from .bridge import Bridge, BridgeEvent
from .config import ChildConfig, RuntimePaths

BridgeFactory = Callable[[str], Bridge]


@dataclass(frozen=True)
class ServerConfig:
    """A2A listener configuration."""

    host: str = "0.0.0.0"
    port: int = 80
    agent_url: str | None = None

    @classmethod
    def from_env(cls) -> "ServerConfig":
        port = int(os.getenv("A2A_PORT", "80"))
        return cls(
            host=os.getenv("A2A_HOST", "0.0.0.0"),
            port=port,
            agent_url=os.getenv("A2A_AGENT_URL") or f"http://localhost:{port}",
        )


@dataclass(frozen=True)
class A2ARequest:
    context_id: str
    message_id: str
    operation_id: str
    task_id: str
    prompt: str


class A2AAdapter:
    """Maps the required A2A message methods to one actor-local bridge."""

    def __init__(self, bridge_factory: BridgeFactory):
        self._bridge_factory = bridge_factory
        self._bridge: Bridge | None = None
        self._lock = asyncio.Lock()

    async def bridge_for(self, context_id: str) -> Bridge:
        async with self._lock:
            if self._bridge is None:
                self._bridge = self._bridge_factory(context_id)
            elif self._bridge.context_id != context_id:
                raise ValueError("durable actor state belongs to a different A2A context")
            return self._bridge

    async def stream(self, request: A2ARequest) -> AsyncIterator[dict[str, Any]]:
        bridge = await self.bridge_for(request.context_id)
        events = bridge.run(
            operation_id=request.operation_id,
            message_id=request.message_id,
            prompt=request.prompt,
        )
        try:
            async for event in events:
                yield _map_event(event, request)
        finally:
            # StreamingResponse closes its body iterator on HTTP disconnect. Closing
            # the bridge iterator invokes its cancel-and-settle path.
            await events.aclose()

    async def send(self, request: A2ARequest) -> Task:
        artifacts: list[Artifact] = []
        terminal: TaskStatusUpdateEvent | None = None
        async for result in self.stream(request):
            event = result["event"]
            if isinstance(event, TaskArtifactUpdateEvent):
                artifacts.append(event.artifact)
            else:
                terminal = event
        if terminal is None:
            raise RuntimeError("bridge stream ended without a terminal event")
        return Task(
            id=request.task_id,
            contextId=request.context_id,
            status=terminal.status,
            artifacts=artifacts or None,
            metadata=terminal.metadata,
        )

    async def close(self) -> None:
        if self._bridge:
            await self._bridge.close()


def _parse_request(params: object) -> A2ARequest:
    if not isinstance(params, dict):
        raise ValueError("params must be an object")
    message = params.get("message")
    if not isinstance(message, dict):
        raise ValueError("params.message must be an object")

    context_id = message.get("contextId") or params.get("contextId")
    message_id = message.get("messageId")
    task_id = message.get("taskId") or params.get("taskId")
    if not isinstance(context_id, str) or not context_id:
        raise ValueError("contextId is required")
    if not isinstance(message_id, str) or not message_id:
        raise ValueError("messageId is required")
    if task_id is not None and (not isinstance(task_id, str) or not task_id):
        raise ValueError("taskId must be a non-empty string")

    parts = message.get("parts")
    if not isinstance(parts, list):
        raise ValueError("message.parts must be an array")
    prompt = "".join(
        part.get("text", "")
        for part in parts
        if isinstance(part, dict) and part.get("kind", part.get("type")) == "text" and isinstance(part.get("text"), str)
    )
    if not prompt:
        raise ValueError("a non-empty text prompt is required")

    operation_id = task_id or message_id
    return A2ARequest(
        context_id=context_id,
        message_id=message_id,
        operation_id=operation_id,
        task_id=operation_id,
        prompt=prompt,
    )


def _message(text: str, request: A2ARequest) -> Message:
    return Message(
        messageId=str(uuid.uuid4()),
        role=Role.agent,
        parts=[Part(TextPart(text=text))],
        contextId=request.context_id,
        taskId=request.task_id,
    )


def _map_event(event: BridgeEvent, request: A2ARequest) -> dict[str, Any]:
    metadata = {
        "operationId": event.operation_id,
        "generation": event.generation,
        "duplicate": event.duplicate,
    }
    if event.kind == "update":
        mapped = TaskArtifactUpdateEvent(
            taskId=request.task_id,
            contextId=request.context_id,
            artifact=Artifact(
                artifactId=f"{request.operation_id}-output",
                parts=[Part(TextPart(text=event.text))],
            ),
            append=True,
            lastChunk=False,
            metadata=metadata,
        )
        return {"event": mapped, "terminal": False}

    states = {
        "completed": TaskState.completed,
        "failed": TaskState.failed,
        "canceled": TaskState.canceled,
        "aborted": TaskState.failed,
        "rejected": TaskState.rejected,
    }
    metadata.update(
        {
            "outcome": event.outcome or "failed",
            "stopReason": event.stop_reason,
        }
    )
    mapped = TaskStatusUpdateEvent(
        taskId=request.task_id,
        contextId=request.context_id,
        status=TaskStatus(
            state=states.get(event.outcome or "failed", TaskState.failed),
            message=_message(event.text, request) if event.text else None,
            timestamp=datetime.now(timezone.utc).isoformat(),
        ),
        final=True,
        metadata=metadata,
    )
    return {"event": mapped, "terminal": True}


def _dump(value: Any) -> dict[str, Any]:
    return value.model_dump(mode="json", by_alias=True, exclude_none=True)


def _jsonrpc_result(request_id: object, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": _dump(result)}


def _jsonrpc_error(request_id: object, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def _agent_card(url: str) -> AgentCard:
    return AgentCard(
        name="Codex ACP Sandbox",
        description="Session-isolated A2A to ACP coding bridge",
        url=url,
        version="0.1.0",
        capabilities=AgentCapabilities(streaming=True),
        defaultInputModes=["text"],
        defaultOutputModes=["text"],
        skills=[
            AgentSkill(
                id="coding",
                name="Coding",
                description="Run a coding task in the actor-local workspace",
                tags=["coding", "acp"],
            )
        ],
    )


def create_app(
    *,
    paths: RuntimePaths | None = None,
    child: ChildConfig | None = None,
    config: ServerConfig | None = None,
    bridge_factory: BridgeFactory | None = None,
) -> Starlette:
    config = config or ServerConfig.from_env()
    paths = paths or RuntimePaths.create(os.getenv("A2A_DATA_DIR", "/data"))
    child = child or ChildConfig.from_env()
    factory = bridge_factory or (lambda context_id: Bridge(context_id=context_id, paths=paths, child=child))
    adapter = A2AAdapter(factory)

    async def agent_card(_request: Request) -> Response:
        try:
            probe = paths.bridge / ".readiness-probe"
            probe.write_text("ready", encoding="utf-8")
            probe.unlink()
        except OSError:
            return JSONResponse({"detail": "bridge state directory is not usable"}, status_code=503)
        if not child.executable_available():
            return JSONResponse({"detail": "ACP child executable is not available"}, status_code=503)
        return JSONResponse(_dump(_agent_card(config.agent_url or f"http://localhost:{config.port}")))

    async def rpc(request: Request) -> Response:
        request_id: object = None
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("request body must be an object")
            request_id = body.get("id")
            method = body.get("method")
            if method in {"tasks/cancel", "tasks/get", "tasks/resubscribe"}:
                return JSONResponse(_jsonrpc_error(request_id, -32601, f"Method not supported: {method}"))
            if method not in {"message/send", "message/stream"}:
                return JSONResponse(_jsonrpc_error(request_id, -32601, f"Method not found: {method}"))
            parsed = _parse_request(body.get("params"))
            if method == "message/send":
                task = await adapter.send(parsed)
                return JSONResponse(_jsonrpc_result(request_id, task))

            async def stream_results() -> AsyncIterator[bytes]:
                async for result in adapter.stream(parsed):
                    payload = {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": _dump(result["event"]),
                    }
                    yield f"data: {json.dumps(payload, separators=(',', ':'))}\n\n".encode()

            return StreamingResponse(stream_results(), media_type="text/event-stream")
        except (ValueError, json.JSONDecodeError) as error:
            return JSONResponse(_jsonrpc_error(request_id, -32602, str(error)))
        except Exception as error:
            return JSONResponse(_jsonrpc_error(request_id, -32603, type(error).__name__))

    @asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await adapter.close()

    app = Starlette(
        routes=[
            Route("/.well-known/agent-card.json", agent_card, methods=["GET"]),
            Route("/", rpc, methods=["POST"]),
        ],
        lifespan=lifespan,
    )
    app.state.adapter = adapter
    return app


def main() -> None:
    config = ServerConfig.from_env()
    uvicorn.run(create_app(config=config), host=config.host, port=config.port)


if __name__ == "__main__":
    main()
