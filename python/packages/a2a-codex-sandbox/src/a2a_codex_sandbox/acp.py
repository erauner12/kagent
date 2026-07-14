"""Direct stdio NDJSON JSON-RPC ACP client."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Awaitable, Callable

from .config import ChildConfig, RuntimePaths
from .diagnostics import Diagnostics

EventHandler = Callable[[dict[str, Any]], Awaitable[None]]


class ACPError(RuntimeError):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class ChildExitedError(RuntimeError):
    def __init__(self, returncode: int):
        super().__init__(f"ACP child exited with status {returncode}")
        self.returncode = returncode


class ACPClient:
    def __init__(
        self,
        child: ChildConfig,
        paths: RuntimePaths,
        diagnostics: Diagnostics,
        on_event: EventHandler,
    ):
        self.child = child
        self.paths = paths
        self.diagnostics = diagnostics
        self.on_event = on_event
        self.process: asyncio.subprocess.Process | None = None
        self._next_id = 1
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._write_lock = asyncio.Lock()

    @property
    def alive(self) -> bool:
        return self.process is not None and self.process.returncode is None

    async def start(self) -> None:
        if self.alive:
            return
        self.process = await asyncio.create_subprocess_exec(
            *self.child.command,
            cwd=self.paths.workspace,
            env=self.child.environment(self.paths),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._reader_task = asyncio.create_task(self._read_stdout())
        self._stderr_task = asyncio.create_task(self._read_stderr())

    async def request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        timeout: float | None,
    ) -> dict[str, Any]:
        if not self.alive:
            raise ChildExitedError(self.process.returncode if self.process else -1)
        request_id = self._next_id
        self._next_id += 1
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[request_id] = future
        await self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        try:
            response = await asyncio.wait_for(asyncio.shield(future), timeout) if timeout else await future
        except asyncio.TimeoutError:
            self._pending.pop(request_id, None)
            self.diagnostics.emit("acp_timeout", method=method)
            raise
        if "error" in response:
            error = response["error"]
            raise ACPError(int(error.get("code", -32000)), str(error.get("message", "ACP request failed")))
        return response.get("result", {})

    async def notify(self, method: str, params: dict[str, Any]) -> None:
        await self._send({"jsonrpc": "2.0", "method": method, "params": params})

    async def initialize(self, timeout: float) -> dict[str, Any]:
        return await self.request(
            "initialize",
            {
                "protocolVersion": 1,
                "clientCapabilities": {
                    "fs": {"readTextFile": True, "writeTextFile": True},
                    "terminal": False,
                },
                "clientInfo": {"name": "a2a-codex-sandbox", "version": "0.1.0"},
            },
            timeout=timeout,
        )

    async def close(self, timeout: float = 1.0) -> None:
        process = self.process
        if process is None:
            return
        if process.stdin:
            process.stdin.close()
        if process.returncode is None:
            try:
                await asyncio.wait_for(process.wait(), timeout)
            except asyncio.TimeoutError:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
        current = asyncio.current_task()
        for task in (self._reader_task, self._stderr_task):
            if task and task is not current and not task.done():
                task.cancel()
        self.process = None

    async def _send(self, message: dict[str, Any]) -> None:
        process = self.process
        if not process or not process.stdin:
            raise ChildExitedError(-1)
        self.diagnostics.envelope("outbound", message)
        encoded = (json.dumps(message, separators=(",", ":")) + "\n").encode()
        async with self._write_lock:
            process.stdin.write(encoded)
            await process.stdin.drain()

    async def _read_stdout(self) -> None:
        assert self.process and self.process.stdout
        while line := await self.process.stdout.readline():
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                self.diagnostics.emit("invalid_json", source="stdout", size=len(line))
                continue
            if not isinstance(message, dict):
                self.diagnostics.emit("invalid_jsonrpc", source="stdout")
                continue
            self.diagnostics.envelope("inbound", message)
            if "method" in message and "id" in message:
                await self._handle_child_request(message)
            elif "method" in message:
                await self.on_event(message)
            elif "id" in message:
                pending = self._pending.pop(message["id"], None)
                if pending and not pending.done():
                    pending.set_result(message)
                else:
                    self.diagnostics.emit("unmatched_response", response_id=message.get("id"))
        returncode = await self.process.wait()
        error = ChildExitedError(returncode)
        for pending in self._pending.values():
            if not pending.done():
                pending.set_exception(error)
        self._pending.clear()
        self.diagnostics.emit("child_exit", returncode=returncode)

    async def _read_stderr(self) -> None:
        assert self.process and self.process.stderr
        while line := await self.process.stderr.readline():
            self.diagnostics.emit("child_stderr", message=line.decode(errors="replace").rstrip())

    async def _handle_child_request(self, message: dict[str, Any]) -> None:
        method = message["method"]
        params = message.get("params", {})
        try:
            if method == "session/request_permission":
                await self.on_event({"method": "bridge/permission_denied", "params": {"method": method}})
                result = {"outcome": {"outcome": "cancelled"}}
            elif method == "elicitation/create":
                await self.on_event({"method": "bridge/permission_denied", "params": {"method": method}})
                result = {"action": "cancel", "content": None}
            elif method == "fs/read_text_file":
                path = self._workspace_path(params["path"])
                content = path.read_text(encoding="utf-8")
                line = max(int(params.get("line", 1)), 1)
                limit = params.get("limit")
                lines = content.splitlines(keepends=True)[line - 1 :]
                if limit is not None:
                    lines = lines[: int(limit)]
                result = {"content": "".join(lines)}
            elif method == "fs/write_text_file":
                path = self._workspace_path(params["path"])
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(str(params["content"]), encoding="utf-8")
                result = {}
            else:
                await self._send(
                    {
                        "jsonrpc": "2.0",
                        "id": message["id"],
                        "error": {"code": -32601, "message": "unsupported ACP client method"},
                    }
                )
                return
            await self._send({"jsonrpc": "2.0", "id": message["id"], "result": result})
        except Exception as error:
            await self._send(
                {
                    "jsonrpc": "2.0",
                    "id": message["id"],
                    "error": {"code": -32603, "message": type(error).__name__},
                }
            )

    def _workspace_path(self, raw_path: str) -> Path:
        path = Path(raw_path)
        if not path.is_absolute():
            path = self.paths.workspace / path
        resolved = path.resolve()
        if resolved != self.paths.workspace and self.paths.workspace not in resolved.parents:
            raise ValueError("ACP file request is outside the durable workspace")
        return resolved
