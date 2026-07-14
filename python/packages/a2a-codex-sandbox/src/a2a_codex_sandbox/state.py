"""Durable session metadata and operation ledger."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import RuntimePaths


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


@dataclass
class SessionState:
    context_id: str
    logical_session_id: str
    workspace_path: str
    acp_session_id: str | None = None
    codex_thread_id: str | None = None
    codex_rollout_path: str | None = None
    session_id_confidence: str = "unavailable"
    prior_acp_session_ids: list[str] = field(default_factory=list)
    prior_codex_thread_ids: list[str] = field(default_factory=list)
    last_completed_operation: str | None = None
    generation: int = 0


class StateStore:
    def __init__(self, paths: RuntimePaths, context_id: str):
        self.paths = paths
        if paths.session_file.exists():
            state = SessionState(**json.loads(paths.session_file.read_text(encoding="utf-8")))
            if state.context_id != context_id:
                raise ValueError("durable actor state belongs to a different A2A context")
            if Path(state.workspace_path).resolve() != paths.workspace:
                raise ValueError("durable workspace mapping does not match runtime workspace")
            self.state = state
        else:
            self.state = SessionState(
                context_id=context_id,
                logical_session_id=str(uuid.uuid4()),
                workspace_path=str(paths.workspace),
            )
            self.save()

    def save(self) -> None:
        _atomic_json(self.paths.session_file, asdict(self.state))


@dataclass
class OperationRecord:
    operation_id: str
    message_id: str
    generation: int
    status: str
    started_at: str
    completed_at: str | None = None
    outcome: str | None = None
    output: str = ""
    stop_reason: str | None = None
    error: str | None = None

    @property
    def terminal(self) -> bool:
        return self.status == "terminal"


class OperationLedger:
    def __init__(self, paths: RuntimePaths):
        self.paths = paths

    def _path(self, operation_id: str) -> Path:
        name = hashlib.sha256(operation_id.encode("utf-8")).hexdigest()
        return self.paths.operations / f"{name}.json"

    def get(self, operation_id: str) -> OperationRecord | None:
        path = self._path(operation_id)
        if not path.exists():
            return None
        return OperationRecord(**json.loads(path.read_text(encoding="utf-8")))

    def active(self) -> OperationRecord | None:
        if not self.paths.active_operation_file.exists():
            return None
        return OperationRecord(**json.loads(self.paths.active_operation_file.read_text(encoding="utf-8")))

    def start(self, operation_id: str, message_id: str, generation: int) -> OperationRecord:
        record = OperationRecord(
            operation_id=operation_id,
            message_id=message_id,
            generation=generation,
            status="active",
            started_at=_now(),
        )
        payload = asdict(record)
        _atomic_json(self._path(operation_id), payload)
        _atomic_json(self.paths.active_operation_file, payload)
        return record

    def settle(
        self,
        operation_id: str,
        generation: int,
        *,
        outcome: str,
        output: str = "",
        stop_reason: str | None = None,
        error: str | None = None,
    ) -> OperationRecord | None:
        active = self.active()
        if not active or active.operation_id != operation_id or active.generation != generation:
            return None
        prior = self.get(operation_id)
        if prior and prior.terminal:
            return prior
        active.status = "terminal"
        active.completed_at = _now()
        active.outcome = outcome
        active.output = output
        active.stop_reason = stop_reason
        active.error = error
        _atomic_json(self._path(operation_id), asdict(active))
        self.paths.active_operation_file.unlink(missing_ok=True)
        return active

    def reconcile_interrupted(self) -> OperationRecord | None:
        active = self.active()
        if not active:
            return None
        return self.settle(
            active.operation_id,
            active.generation,
            outcome="aborted",
            error="bridge restarted while operation was active",
        )
