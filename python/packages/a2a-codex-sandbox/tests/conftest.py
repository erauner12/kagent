from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest_asyncio

from a2a_codex_sandbox.bridge import Bridge, BridgeConfig
from a2a_codex_sandbox.config import ChildConfig, RuntimePaths


@pytest_asyncio.fixture
async def bridge_factory(tmp_path: Path):
    bridges: list[Bridge] = []

    def create(
        scenario: str = "normal",
        *,
        context_id: str = "context-1",
        data: Path | None = None,
        control_timeout: float = 1.0,
        cancel_timeout: float = 0.5,
        require_credentials: bool = False,
    ) -> Bridge:
        root = data or tmp_path / context_id
        paths = RuntimePaths.create(root)
        source = Path(__file__).parents[1] / "src"
        pythonpath = str(source)
        if os.getenv("PYTHONPATH"):
            pythonpath += os.pathsep + os.environ["PYTHONPATH"]
        child = ChildConfig(
            command=(sys.executable, "-m", "a2a_codex_sandbox.fake_acp"),
            extra_env={
                "FAKE_ACP_SCENARIO": scenario,
                "FAKE_ACP_TRACE": str(paths.bridge / "fake-trace.jsonl"),
                "PYTHONPATH": pythonpath,
            },
            require_credentials=require_credentials,
        )
        bridge = Bridge(
            context_id=context_id,
            paths=paths,
            child=child,
            config=BridgeConfig(
                control_timeout=control_timeout,
                cancel_timeout=cancel_timeout,
                teardown_timeout=0.2,
            ),
        )
        bridges.append(bridge)
        return bridge

    yield create

    for bridge in bridges:
        await bridge.close()
