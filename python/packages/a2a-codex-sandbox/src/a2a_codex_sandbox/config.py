"""Runtime path and ACP child configuration."""

from __future__ import annotations

import os
import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path


class MissingCredentialsError(RuntimeError):
    """Raised when a credentialed child is invoked without credentials."""


@dataclass(frozen=True)
class RuntimePaths:
    data: Path
    workspace: Path
    bridge: Path
    operations: Path
    codex_home: Path
    session_file: Path
    active_operation_file: Path
    diagnostics_file: Path

    @classmethod
    def create(cls, data: Path | str = "/data") -> "RuntimePaths":
        root = Path(data).resolve()
        paths = cls(
            data=root,
            workspace=root / "workspace",
            bridge=root / "bridge",
            operations=root / "bridge" / "operations",
            codex_home=root / "codex",
            session_file=root / "bridge" / "session.json",
            active_operation_file=root / "bridge" / "active-operation.json",
            diagnostics_file=root / "bridge" / "diagnostics.jsonl",
        )
        for directory in (paths.data, paths.workspace, paths.bridge, paths.operations, paths.codex_home):
            directory.mkdir(parents=True, exist_ok=True)
        probe = paths.bridge / ".write-probe"
        probe.write_text("ready", encoding="utf-8")
        probe.unlink()
        return paths


@dataclass(frozen=True)
class ChildConfig:
    command: tuple[str, ...]
    extra_env: dict[str, str]
    require_credentials: bool = False

    @classmethod
    def from_env(cls) -> "ChildConfig":
        command = tuple(shlex.split(os.getenv("ACP_CHILD_COMMAND", "codex-acp")))
        if not command:
            raise ValueError("ACP_CHILD_COMMAND must not be empty")
        return cls(
            command=command,
            extra_env={},
            require_credentials=os.getenv("ACP_REQUIRE_CREDENTIALS", "true").lower() == "true",
        )

    def executable_available(self) -> bool:
        executable = self.command[0]
        if os.path.isabs(executable) or "/" in executable:
            return Path(executable).is_file() and os.access(executable, os.X_OK)
        return shutil.which(executable) is not None

    def environment(self, paths: RuntimePaths) -> dict[str, str]:
        env = os.environ.copy()
        env.update(self.extra_env)
        env["CODEX_HOME"] = str(paths.codex_home)
        env.setdefault("NO_BROWSER", "1")
        return env

    def validate_credentials(self, paths: RuntimePaths) -> None:
        if not self.require_credentials:
            return
        env = self.environment(paths)
        if not env.get("CODEX_API_KEY") and not env.get("OPENAI_API_KEY"):
            raise MissingCredentialsError("ACP child credentials are not configured")
