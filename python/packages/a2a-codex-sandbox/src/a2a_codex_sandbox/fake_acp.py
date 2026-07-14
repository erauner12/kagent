"""Deterministic ACP stdio child used by model-independent contract tests."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

SCENARIO = set(filter(None, os.getenv("FAKE_ACP_SCENARIO", "normal").split(",")))
TRACE = os.getenv("FAKE_ACP_TRACE")
SERVER_REQUEST_ID = 9000


def send(message: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def trace(method: str, **fields: Any) -> None:
    if not TRACE:
        return
    with Path(TRACE).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"method": method, **fields}, sort_keys=True) + "\n")


def response(request_id: int, result: dict[str, Any]) -> None:
    send({"jsonrpc": "2.0", "id": request_id, "result": result})


def error(request_id: int, code: int, message: str) -> None:
    send({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}})


def update(session_id: str, text: str) -> None:
    send(
        {
            "jsonrpc": "2.0",
            "method": "session/update",
            "params": {
                "sessionId": session_id,
                "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": text},
                },
            },
        }
    )


def child_request(method: str, params: dict[str, Any]) -> dict[str, Any]:
    global SERVER_REQUEST_ID
    SERVER_REQUEST_ID += 1
    request_id = SERVER_REQUEST_ID
    send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
    while line := sys.stdin.readline():
        incoming = json.loads(line)
        if incoming.get("id") == request_id and "method" not in incoming:
            trace(f"{method}/response", result=incoming.get("result"))
            return incoming.get("result", {})
    raise SystemExit(0)


def prompt_text(params: dict[str, Any]) -> str:
    parts = params.get("prompt", [])
    return "".join(part.get("text", "") for part in parts if isinstance(part, dict))


def main() -> None:
    session_number = 0
    while line := sys.stdin.readline():
        message = json.loads(line)
        method = message.get("method")
        params = message.get("params", {})
        request_id = message.get("id")
        trace(method or "response")

        if method == "initialize":
            if "hang_initialize" in SCENARIO:
                continue
            if "fail_initialize" in SCENARIO:
                error(request_id, -32000, "initialization rejected")
                continue
            response(
                request_id,
                {
                    "protocolVersion": 1,
                    "agentCapabilities": {"loadSession": True, "promptCapabilities": {"image": False}},
                    "authMethods": [],
                    "agentInfo": {"name": "fake-acp", "version": "1"},
                },
            )
        elif method == "session/new":
            if "hang_new" in SCENARIO:
                continue
            session_number += 1
            session_id = f"fake-session-{session_number}"
            response(request_id, {"sessionId": session_id, "threadId": session_id})
        elif method == "session/load":
            session_id = params["sessionId"]
            if "hang_load" in SCENARIO:
                continue
            if "load_not_found" in SCENARIO:
                error(request_id, -32600, f"thread not found: {session_id}")
            elif "load_auth_error" in SCENARIO:
                error(request_id, -32001, "authentication required")
            else:
                update(session_id, "historical replay")
                response(request_id, {"sessionId": session_id, "threadId": session_id})
        elif method == "session/prompt":
            session_id = params["sessionId"]
            text = prompt_text(params)
            if "diagnostics" in SCENARIO:
                sys.stderr.write("authorization: super-secret-token\n")
                sys.stderr.flush()
                sys.stdout.write("not-json\n")
                sys.stdout.flush()
                response(424242, {"ignored": True})
            if "controlled_failure" in SCENARIO or "fail" in text:
                error(request_id, -32010, "controlled prompt failure")
                continue
            if "crash" in SCENARIO:
                os._exit(3)
            if "clean_exit" in SCENARIO:
                raise SystemExit(0)
            if "permission" in text:
                child_request(
                    "session/request_permission",
                    {
                        "sessionId": session_id,
                        "toolCall": {"toolCallId": "tool-1", "kind": "execute", "status": "pending"},
                        "options": [{"optionId": "allow", "name": "Allow", "kind": "allow_once"}],
                    },
                )
            if "elicitation" in text:
                child_request(
                    "elicitation/create",
                    {
                        "sessionId": session_id,
                        "message": "Need input",
                        "requestedSchema": {"type": "object"},
                    },
                )
            if "workspace" in text:
                marker = str(Path.cwd() / "marker.txt")
                child_request(
                    "fs/write_text_file",
                    {"sessionId": session_id, "path": marker, "content": "workspace-ok"},
                )
                result = child_request(
                    "fs/read_text_file",
                    {"sessionId": session_id, "path": marker},
                )
                update(session_id, result.get("content", ""))
            if text.startswith("marker-write:"):
                marker_value = text.removeprefix("marker-write:")
                (Path.cwd() / "marker.txt").write_text(marker_value, encoding="utf-8")
                update(session_id, f"marker:{marker_value}")
            if text == "marker-read":
                marker_value = (Path.cwd() / "marker.txt").read_text(encoding="utf-8")
                update(session_id, f"marker:{marker_value}")
            if text == "evidence":
                update(
                    session_id,
                    json.dumps(
                        {"acpSessionId": session_id, "workspace": str(Path.cwd())},
                        sort_keys=True,
                    ),
                )
            update(session_id, "hello ")
            update(session_id, "world")
            if "hang_prompt" in SCENARIO or "cancel" in text:
                while cancel_line := sys.stdin.readline():
                    cancel = json.loads(cancel_line)
                    trace(cancel.get("method", "response"))
                    if cancel.get("method") == "session/cancel":
                        response(request_id, {"stopReason": "cancelled"})
                        break
                continue
            response(request_id, {"stopReason": "end_turn"})
        elif method == "session/cancel":
            continue


if __name__ == "__main__":
    main()
