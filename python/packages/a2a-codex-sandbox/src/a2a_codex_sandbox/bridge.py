"""Session-isolated bridge lifecycle and A2A-neutral event model."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import AsyncIterator, Literal

from .acp import ACPClient, ACPError
from .config import ChildConfig, RuntimePaths
from .diagnostics import Diagnostics
from .state import OperationLedger, OperationRecord, StateStore

Outcome = Literal["completed", "failed", "canceled", "aborted", "rejected"]


@dataclass(frozen=True)
class BridgeConfig:
    control_timeout: float = 10.0
    cancel_timeout: float = 2.0
    teardown_timeout: float = 1.0


@dataclass(frozen=True)
class BridgeEvent:
    kind: Literal["update", "terminal"]
    operation_id: str
    generation: int
    text: str = ""
    outcome: Outcome | None = None
    stop_reason: str | None = None
    duplicate: bool = False


class Bridge:
    """Owns one actor-local context, workspace, child, and logical session."""

    def __init__(
        self,
        *,
        context_id: str,
        paths: RuntimePaths,
        child: ChildConfig,
        config: BridgeConfig | None = None,
    ):
        if not context_id.strip():
            raise ValueError("context_id is required")
        self.paths = paths
        self.child = child
        self.config = config or BridgeConfig()
        self.store = StateStore(paths, context_id)
        self.ledger = OperationLedger(paths)
        self.diagnostics = Diagnostics(paths.diagnostics_file)
        interrupted = self.ledger.reconcile_interrupted()
        if interrupted:
            self.diagnostics.emit(
                "operation_reconciled",
                outcome="aborted",
                generation=interrupted.generation,
            )
        self._client: ACPClient | None = None
        self._lifecycle_lock = asyncio.Lock()
        self._active_generation: int | None = None
        self._active_operation_id: str | None = None
        self._active_prompt: asyncio.Task[dict] | None = None
        self._event_queue: asyncio.Queue[BridgeEvent] | None = None
        self._loading = False

    @property
    def context_id(self) -> str:
        return self.store.state.context_id

    @property
    def generation(self) -> int:
        return self.store.state.generation

    async def close(self) -> None:
        generation = self._active_generation
        if generation is not None and self._active_prompt and not self._active_prompt.done():
            await self._cancel_active(generation)
        if self._client:
            await self._client.close(self.config.teardown_timeout)
            self._client = None

    async def run(
        self,
        *,
        operation_id: str,
        message_id: str,
        prompt: str,
    ) -> AsyncIterator[BridgeEvent]:
        prior = self.ledger.get(operation_id)
        if prior and prior.terminal:
            yield self._terminal_from_record(prior, duplicate=True)
            return

        async with self._lifecycle_lock:
            prior = self.ledger.get(operation_id)
            if prior and prior.terminal:
                yield self._terminal_from_record(prior, duplicate=True)
                return
            active = self.ledger.active()
            if active or self._active_generation is not None:
                yield BridgeEvent(
                    kind="terminal",
                    operation_id=operation_id,
                    generation=self.generation,
                    outcome="rejected",
                    text="Another prompt is already active for this session.",
                    stop_reason="busy",
                )
                return
            self.store.state.generation += 1
            generation = self.store.state.generation
            self.store.save()
            self.ledger.start(operation_id, message_id, generation)
            self._active_generation = generation
            self._active_operation_id = operation_id
            self._event_queue = asyncio.Queue()
            self.diagnostics.emit("generation_start", generation=generation)

        output: list[str] = []
        completed = False
        prompt_task: asyncio.Task[dict] | None = None
        try:
            await self._bootstrap()
            assert self._client
            session_id = self.store.state.acp_session_id
            if not session_id:
                raise RuntimeError("ACP session was not established")
            # The durable active record and backend mapping are both persisted before this call.
            prompt_task = asyncio.create_task(
                self._client.request(
                    "session/prompt",
                    {
                        "sessionId": session_id,
                        "prompt": [{"type": "text", "text": prompt}],
                    },
                    timeout=None,
                )
            )
            self._active_prompt = prompt_task
            assert self._event_queue
            while True:
                queue_task = asyncio.create_task(self._event_queue.get())
                done, _ = await asyncio.wait(
                    {prompt_task, queue_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if queue_task in done:
                    event = queue_task.result()
                    if event.text:
                        output.append(event.text)
                    yield event
                else:
                    queue_task.cancel()
                    try:
                        await queue_task
                    except asyncio.CancelledError:
                        pass

                if prompt_task in done:
                    while not self._event_queue.empty():
                        event = self._event_queue.get_nowait()
                        if event.text:
                            output.append(event.text)
                        yield event
                    result = prompt_task.result()
                    stop_reason = result.get("stopReason")
                    if not isinstance(stop_reason, str) or not stop_reason:
                        raise RuntimeError("session/prompt response omitted terminal stopReason")
                    outcome: Outcome = "canceled" if stop_reason in {"cancelled", "canceled"} else "completed"
                    record = self._settle(
                        operation_id,
                        generation,
                        outcome=outcome,
                        output="".join(output),
                        stop_reason=stop_reason,
                    )
                    if record:
                        completed = True
                        yield self._terminal_from_record(record)
                    return
        except (GeneratorExit, asyncio.CancelledError):
            raise
        except Exception as error:
            record = self._settle(
                operation_id,
                generation,
                outcome="failed",
                output="".join(output),
                error=type(error).__name__,
            )
            if record:
                completed = True
                yield self._terminal_from_record(record)
        finally:
            if not completed and self._owns(generation, operation_id):
                await self._cancel_active(generation)
            if self._active_generation == generation:
                self._active_generation = None
                self._active_operation_id = None
                self._active_prompt = None
                self._event_queue = None

    async def cancel(self, generation: int) -> bool:
        if generation != self._active_generation:
            self.diagnostics.emit("stale_cancel_ignored", generation=generation)
            return False
        await self._cancel_active(generation)
        return True

    async def teardown_generation(self, generation: int) -> bool:
        if generation != self._active_generation:
            self.diagnostics.emit("stale_teardown_ignored", generation=generation)
            return False
        if self._client:
            await self._client.close(self.config.teardown_timeout)
            self._client = None
        self.diagnostics.emit("generation_teardown", generation=generation)
        return True

    async def _bootstrap(self) -> None:
        if self._client and self._client.alive and self.store.state.acp_session_id:
            return
        self.child.validate_credentials(self.paths)
        self._client = ACPClient(
            self.child,
            self.paths,
            self.diagnostics,
            self._on_acp_event,
        )
        try:
            await self._client.start()
            await self._client.initialize(self.config.control_timeout)
            candidate = self.store.state.acp_session_id or self.store.state.codex_thread_id
            if candidate:
                self._loading = True
                try:
                    result = await self._client.request(
                        "session/load",
                        {
                            "sessionId": candidate,
                            "cwd": str(self.paths.workspace),
                            "mcpServers": [],
                        },
                        timeout=self.config.control_timeout,
                    )
                except ACPError as error:
                    expected = error.code == -32600 and f"thread not found: {candidate}" in error.message
                    if not expected:
                        raise
                    self.diagnostics.emit("load_fallback", reason="thread_not_found")
                    if candidate not in self.store.state.prior_acp_session_ids:
                        self.store.state.prior_acp_session_ids.append(candidate)
                    previous_thread = self.store.state.codex_thread_id
                    if previous_thread and previous_thread not in self.store.state.prior_codex_thread_ids:
                        self.store.state.prior_codex_thread_ids.append(previous_thread)
                    self.store.state.acp_session_id = None
                    self.store.state.codex_thread_id = None
                    self.store.state.session_id_confidence = "unavailable"
                    self.store.save()
                    await self._new_session()
                else:
                    session_id = str(result.get("sessionId", candidate))
                    self._record_backend_session(session_id, confidence="verified", result=result)
                finally:
                    self._loading = False
            else:
                await self._new_session()
        except Exception:
            await self._client.close(self.config.teardown_timeout)
            self._client = None
            raise

    async def _new_session(self) -> None:
        assert self._client
        result = await self._client.request(
            "session/new",
            {"cwd": str(self.paths.workspace), "mcpServers": []},
            timeout=self.config.control_timeout,
        )
        session_id = result.get("sessionId")
        if not isinstance(session_id, str) or not session_id:
            raise RuntimeError("session/new response omitted sessionId")
        self._record_backend_session(session_id, confidence="candidate", result=result)

    def _record_backend_session(self, session_id: str, *, confidence: str, result: dict) -> None:
        previous = self.store.state.acp_session_id
        previous_thread = self.store.state.codex_thread_id
        if previous and previous != session_id and previous not in self.store.state.prior_acp_session_ids:
            self.store.state.prior_acp_session_ids.append(previous)
        thread_id = str(result.get("threadId", session_id))
        if (
            previous_thread
            and previous_thread != thread_id
            and previous_thread not in self.store.state.prior_codex_thread_ids
        ):
            self.store.state.prior_codex_thread_ids.append(previous_thread)
        self.store.state.acp_session_id = session_id
        self.store.state.codex_thread_id = thread_id
        rollout = result.get("rolloutPath")
        self.store.state.codex_rollout_path = str(rollout) if rollout else None
        self.store.state.session_id_confidence = confidence
        self.store.save()

    async def _on_acp_event(self, message: dict) -> None:
        if self._loading and message.get("method") == "session/update":
            self.diagnostics.emit("load_replay_suppressed")
            return
        queue = self._event_queue
        generation = self._active_generation
        operation_id = self._active_operation_id
        if not queue or generation is None or operation_id is None:
            self.diagnostics.emit("unowned_acp_event", method=message.get("method"))
            return
        if message.get("method") == "bridge/permission_denied":
            text = "Permission request denied by bridge policy."
        elif message.get("method") == "session/update":
            text = self._update_text(message.get("params", {}).get("update"))
            if not text:
                return
        else:
            return
        await queue.put(
            BridgeEvent(
                kind="update",
                operation_id=operation_id,
                generation=generation,
                text=text,
            )
        )

    @staticmethod
    def _update_text(update: object) -> str:
        if not isinstance(update, dict):
            return ""
        content = update.get("content")
        if isinstance(content, dict) and isinstance(content.get("text"), str):
            return content["text"]
        if isinstance(content, list):
            return "".join(
                part.get("text", "") for part in content if isinstance(part, dict) and isinstance(part.get("text"), str)
            )
        if isinstance(update.get("text"), str):
            return update["text"]
        return ""

    async def _cancel_active(self, generation: int) -> None:
        operation_id = self._active_operation_id
        if not operation_id or not self._owns(generation, operation_id):
            return
        prompt_task = self._active_prompt
        if self._client and self._client.alive and self.store.state.acp_session_id:
            try:
                await self._client.notify(
                    "session/cancel",
                    {"sessionId": self.store.state.acp_session_id},
                )
            except Exception:
                pass
        if prompt_task and not prompt_task.done():
            try:
                await asyncio.wait_for(asyncio.shield(prompt_task), self.config.cancel_timeout)
            except (asyncio.TimeoutError, Exception):
                pass
        if prompt_task and not prompt_task.done():
            await self.teardown_generation(generation)
            prompt_task.cancel()
        self._settle(
            operation_id,
            generation,
            outcome="canceled",
            stop_reason="cancelled",
        )

    def _settle(
        self,
        operation_id: str,
        generation: int,
        *,
        outcome: Outcome,
        output: str = "",
        stop_reason: str | None = None,
        error: str | None = None,
    ) -> OperationRecord | None:
        record = self.ledger.settle(
            operation_id,
            generation,
            outcome=outcome,
            output=output,
            stop_reason=stop_reason,
            error=error,
        )
        if record:
            self.store.state.last_completed_operation = operation_id
            self.store.save()
            self.diagnostics.emit("generation_settled", generation=generation, outcome=outcome)
        else:
            self.diagnostics.emit("stale_settlement_ignored", generation=generation)
        return record

    def _owns(self, generation: int, operation_id: str) -> bool:
        return self._active_generation == generation and self._active_operation_id == operation_id

    @staticmethod
    def _terminal_from_record(record: OperationRecord, duplicate: bool = False) -> BridgeEvent:
        outcome: Outcome = (
            record.outcome
            if record.outcome
            in {
                "completed",
                "failed",
                "canceled",
                "aborted",
                "rejected",
            }
            else "failed"
        )
        text = record.output
        if not text and record.error:
            text = f"ACP operation failed ({record.error})."
        return BridgeEvent(
            kind="terminal",
            operation_id=record.operation_id,
            generation=record.generation,
            text=text,
            outcome=outcome,
            stop_reason=record.stop_reason,
            duplicate=duplicate,
        )
