# Fake ACP Kind e2e evidence

- Date (UTC): 2026-07-14T23:19:24Z
- Base kagent commit: 1e512d79fe42514a19e230bb01ff6a7fc1bd9b51
- Prerequisite packaging commit: `e2ae8df0`
- Proof classification: **fake-runtime / local Kind**
- Image proof: **local-registry pull** (built with `--load`, pushed to the Kind-local registry, then pulled by the WorkerPool; not remote registry publication proof)
- Image tag: `localhost:5001/kagent-dev/kagent/a2a-codex-sandbox:v0.9.10-99-g1e512d79`
- Local image ID: `sha256:1f6ecc2c7934dd90a1d5c124f46ccddf3bacdbc0c5a48fdfc38a96199c0d0e28`
- WorkerPool image ref: `localhost:5001/kagent-dev/kagent/a2a-codex-sandbox@sha256:1f6ecc2c7934dd90a1d5c124f46ccddf3bacdbc0c5a48fdfc38a96199c0d0e28`
- WorkerPool: `kagent/kagent-default`
- ActorTemplate: `kagent/a2a-codex-sandbox-fake-6214409a7f3b2016`
- Context A / actor / workspace / actor-scoped logical ACP session: `20260714231830:A` / `asr-kagent-a2a-codex-sandbox-fake-fake-a-20260714231830` / actor-local `/data/workspace` / `fake-session-1`
- Context B / actor / workspace / actor-scoped logical ACP session: `20260714231830:B` / `asr-kagent-a2a-codex-sandbox-fake-fake-b-20260714231830` / actor-local `/data/workspace` / `fake-session-1`
- Readiness: Ready ActorTemplate and SandboxAgent observed.
- Prompt streaming: multiple artifact updates and exactly one final completed status per successful request.
- Workspace isolation: marker `actor-a` remained visible only in context A; marker `actor-b` remained visible only in context B.
- Suspend/resume: context A reached a suspended/paused state, resumed, and retained its actor-local workspace marker.
- Immediate back-to-back turns: completed without a suspend race failure and retained context A's marker.
- Disconnect-as-cancel: **not verified**. The client stream was terminated after its first update, but five duplicate replays did not expose a canceled terminal record through the local port-forward/A2A proxy. This remains a targeted follow-up.
- Session deletion: context A actor disappeared; context B remained usable with its marker intact.
- Cleanup: both session actors, the SandboxAgent, and its ActorTemplate were removed. The local image and shared WorkerPool were intentionally retained.
- Redaction: generated test identifiers only; no credentials, sensitive prompts, environment dumps, or workspace contents were captured.
- Not proven here: remote registry publication/index digest, credentialed Codex ACP, or parent-Agent delegation.

Logical-session IDs are child-local; the distinct actor/session tuples above are the globally unique correlation keys.

## Disconnect follow-up checkpoint (2026-07-14)

- Command: `KIND_CLUSTER_NAME=kg-yul1 EVIDENCE_FILE=/tmp/session-isolated-acp-fake-kind-baseline.md ./scripts/kind-e2e-session-isolated-acp-fake.sh`
- Base kagent commit: `366fb601ee659c832aa0b85f349a5771d4ef195f`
- Result: the full fake-runtime/local Kind lane completed successfully, but disconnect-as-cancel remained **not verified**. After the port-forwarded client was killed following its first SSE update, all five duplicate replays failed to expose a terminal record with `outcome=canceled` and `duplicate=true`.
- Narrow-path check: `test_disconnect_sends_cancel_and_settles` and `test_adapter_generator_close_cancels_bridge` already prove that closing the runtime/adapter iterator sends ACP `session/cancel`, settles the durable operation as canceled, and makes it replayable. The live gap is therefore before iterator close, in observing/propagating the downstream disconnect through the local `kubectl port-forward` and A2A proxy path.
- In-cluster probe: a replacement client Pod was attempted to remove port-forward from the downstream leg, but it reached `StartError` because the test image does not contain `/python/bin/python`. The attempt was stopped without adding a new image/tooling path or redesigning the proxy.
- Proof classification: **limitation / negative evidence**. The current port-forward harness cannot establish disconnect propagation, so task `4.3b` remains unchecked.
