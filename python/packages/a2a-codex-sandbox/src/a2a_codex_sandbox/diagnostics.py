"""Persistent redacted diagnostics for the bridge."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SECRET_PATTERN = re.compile(r"(?i)(bearer\s+|(?:api[_-]?key|token|authorization|password)[=:\s]+)([^\s,;]+)")


class Diagnostics:
    def __init__(self, path: Path):
        self.path = path
        self.events: list[dict[str, Any]] = []

    def emit(self, kind: str, **fields: Any) -> None:
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "kind": kind,
            **{key: self._redact(value) for key, value in fields.items()},
        }
        self.events.append(event)
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, sort_keys=True) + "\n")

    def envelope(self, direction: str, message: dict[str, Any]) -> None:
        self.emit(
            "acp_jsonrpc",
            direction=direction,
            envelope_kind=(
                "request"
                if "method" in message and "id" in message
                else "notification"
                if "method" in message
                else "response"
            ),
            method=message.get("method"),
            response_id=message.get("id"),
            has_error="error" in message,
        )

    @classmethod
    def _redact(cls, value: Any) -> Any:
        if isinstance(value, str):
            return _SECRET_PATTERN.sub(lambda match: match.group(1) + "[REDACTED]", value)[:1000]
        if isinstance(value, dict):
            return {
                key: "[REDACTED]"
                if any(
                    secret in key.lower()
                    for secret in ("key", "token", "authorization", "password", "prompt", "content")
                )
                else cls._redact(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [cls._redact(item) for item in value]
        return value
