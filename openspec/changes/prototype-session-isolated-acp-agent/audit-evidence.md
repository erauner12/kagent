# PR 1 audit evidence and decisions (tasks 1.1–1.7)

Audit date: 2026-07-14

This record is source-level and fixture-backed. It does not claim a built bridge image, a Kind deployment, a credentialed Codex turn, or parent-Agent end-to-end acceptance.

## Pins and proof classification

| Item | Audited value |
|---|---|
| PR branch / pre-audit HEAD | `pr/session-isolated-acp-agent-audits` / `0e27e4665f0fa55936c8fa4842b323772a870e1e` |
| PR base | `dev/session-isolated-acp-agent` at the same commit; verified as an ancestor |
| Codex sandbox prerequisite | `f7412069030dfef2a0b5df774c32ebb41c3f1398`; verified as an ancestor |
| Selected kagent source/chart | `0e27e4665f0fa55936c8fa4842b323772a870e1e`; chart version is stamped from build `VERSION` |
| Substrate dependency | `github.com/kagent-dev/substrate v0.0.8` |
| Codex ACP adapter | npm `@agentclientprotocol/codex-acp@1.1.2`; source tag `8aff492d4b033ff2c02ad3b9d591994d57617463` |
| Codex dependency in the 1.1.2 source lock | `@openai/codex@0.144.0` |
| Proof class | structural/source audit plus existing Go/Python unit fixtures |

The sandbox Dockerfile pins adapter 1.1.2 but uses `npm install -g`. The adapter declares `@openai/codex` as `^0.144.0`, so the adapter pin alone does not make the transitive Codex binary immutable. PR 2 must record the resolved Codex version and make the image build reproducible before accepting its digest.

## 1.1 — packaging prerequisite

**Complete.** Branch history contains merge `e0686a50` and prerequisite `f7412069` (`feat: add Codex ACP sandbox image`). The prerequisite adds the `codex` Docker target, `build-acp-sandbox-codex`, and CI/tag matrix entries. The future A2A runtime remains a distinct image.

## 1.2 — selected controller/chart and transport

**Complete, structurally verified.**

- `go/api/v1alpha2/agent_spec_validation.go` requires `spec.byo.deployment.cmd` for substrate BYO agents.
- `go/core/pkg/sandboxbackend/substrate/agent_lifecycle.go` copies BYO command/args into the ActorTemplate, pins the workload image reference, mounts durable `/data`, and probes `/.well-known/agent-card.json` on port 80.
- `go/core/internal/a2a/substrate_sandbox_transport.go` extracts only body `params.message.contextId` or `params.contextId`. Missing/blank context fails before actor creation or bridge execution with: `message contextId (session id) is required for substrate sandbox agents`.
- Standard `tasks/resubscribe`, `tasks/get`, and `tasks/cancel` requests identify a task by `params.id`, so they do not satisfy this body-context gate. The UI issues resubscribe; current kagent clients do not issue task cancel through this route.

This is source compatibility evidence, not a deployed controller-image or WorkerPool-pull claim.

## 1.3 — A2A method surface

**Complete.**

| Path | Method | Evidence |
|---|---|---|
| Direct UI/CLI SandboxAgent chat | `message/stream` | UI creates that JSON-RPC method; CLI uses streaming send |
| Go parent Agent tool | `message/send` | `remote_a2a_tool.go` calls non-streaming `SendMessage` |
| Python parent Agent tool | `message/send` | client sets `streaming=False` and calls `send_message` |
| Task control plane | not routable for this POC | task-id-only bodies do not satisfy the substrate context gate |

Decision: implement `message/stream` and `message/send` only. Cancellation is stream disconnect/response close. Task-id-only control methods require a later kagent core routing change.

## 1.4 — parent correlation audit

**Complete using existing outbound-message/header fixtures and construction-path inspection.** No echo fixture was needed.

| Parent runtime | Same parent turn | Cross-turn behavior | Lineage headers |
|---|---|---|---|
| Go | Calls use the tool state's generated `lastContextID` | The root agent/tool is constructed once per process, so the child body `contextId` is reused across turns and unrelated parent conversations in that process | parent = current parent session; root = inbound root or parent fallback |
| Python | Calls in one request-local runner/toolset reuse `_last_context_id` | `create_runner()` calls `root_agent_factory()` for every A2A execution, so a later turn gets a new child body `contextId` | parent = current parent session; root = inbound root or parent fallback |

Fixtures verify generated IDs in outbound bodies and `x-kagent-parent-context-id` / `x-kagent-root-context-id` on outbound HTTP. Headers do not affect substrate routing.

Decision: before parent acceptance, both parent tools derive child body `contextId` deterministically from **root context ID + remote SandboxAgent namespace/name**, using a namespaced hash/UUID derivation valid as an A2A ID. Missing root falls back to immediate parent context. Raw IDs are not logged. This is PR 2+ caller work; the bridge cannot repair correlation because routing occurs first.

## 1.5 — bounded language spike

**Complete.**

| Criterion | Python | Go |
|---|---|---|
| A2A server | Existing `a2a-sdk`, FastAPI/Uvicorn, and kagent runtime patterns | Existing `a2a-go` and Go ADK patterns |
| ACP client | Thin async NDJSON JSON-RPC client required | Thin NDJSON JSON-RPC client required |
| Supervision/cancel | `asyncio` subprocess/tasks map directly | `os/exec`, goroutines, contexts are strong |
| Fake child | Straightforward pytest subprocess fixture | Straightforward Go helper fixture |
| Ownership/reuse | Matches experimental BYO runtime guidance | Better for later controller/core migration |

Decision: **Python**. Both languages need similarly thin ACP code; the repository boundary favors Python. TypeScript is rejected—the Node child does not require a Node bridge.

## 1.6 — source/image ownership

**Complete as a decision; no image was built.**

- Source: `python/packages/a2a-codex-sandbox/`.
- Image: `a2a-codex-sandbox`.
- Docker/build: `docker/a2a-codex-sandbox/Dockerfile` and root target `build-a2a-codex-sandbox`.
- Published repository: `ghcr.io/kagent-dev/kagent/a2a-codex-sandbox`; existing `DOCKER_REGISTRY` remains the local override.
- Architectures: Linux multi-arch index for `linux/amd64` and `linux/arm64`; claim an architecture only after its lane passes.
- Pinning: manifests/evidence use the index digest (`@sha256:...`), record per-platform digests and resolved adapter/Codex versions; tags are convenience only.
- Ownership: this kagent fork owns source, Dockerfile, build, tests, and examples. `kagent-garden` may later consume only a pinned digest.

Registry access, WorkerPool pull, and final digests remain later validation.

## 1.7 — pinned codex-acp behavior

**Complete as a 1.1.2 source audit; credentialed/restart proof remains task 4.x.**

- **Direct stdio:** `src/index.ts` connects ACP NDJSON to stdin/stdout and spawns `codex app-server`. Closing ACP stdin ends, then force-terminates, the child.
- **Authentication:** initialize advertises API-key auth, hides browser login with `NO_BROWSER=1`, and uses `CODEX_API_KEY` with `OPENAI_API_KEY` fallback. Session creation checks authorization.
- **New/load/resume:** `session/new` calls `thread/start` and returns the Codex thread ID as ACP `sessionId`. Load and resume pass that ID to `thread/resume`; load also calls `thread/read(includeTurns=true)`.
- **Load-not-found:** the adapter does not normalize a stable type; it propagates the Codex App Server rejection. The audited Codex contract uses JSON-RPC code `-32600` and message `thread not found: <id>`. Fallback is allowed only for that pinned code/message (or a later explicitly tested equivalent), never for auth, transport, or arbitrary load errors.
- **Load replay:** load converts stored history to ACP `session/update` notifications before returning. Suppress these from fresh A2A output until load completes.
- **Cancellation:** ACP `session/cancel` calls `turn/interrupt`; prompt settlement returns stop reason `cancelled`. On disconnect, send cancel and await bounded settlement, then tear down on failure.
- **Permissions:** command, file-change, and permission-profile requests use `session/request_permission`. MCP elicitation may use ACP elicitation or permission requests according to client capability. The first bridge denies/cancels and never waits for a human.
- **Rollout path:** ACP 1.1.2 does not expose rollout JSONL path. Persist thread ID and report rollout path as unavailable; do not invent one.
- **Restart:** adapter session state is in memory. Restart requires initialize then load/resume using persisted thread ID. Set `CODEX_HOME=/data/codex` before child start so Codex can find durable state. Conversational continuity remains unproven until later restart smoke coverage.
- **Pin policy:** retain adapter 1.1.2 for PR 2 unless a separately reviewed upgrade is required; also make the resolved `@openai/codex` version reproducible.

## Commands run

```text
git merge-base --is-ancestor dev/session-isolated-acp-agent HEAD
git merge-base --is-ancestor f7412069 HEAD
go test ./api/v1alpha2
go test ./core/pkg/sandboxbackend/substrate
go test ./core/internal/a2a
go test ./adk/pkg/tools
uv run pytest packages/kagent-adk/tests/unittests/test_remote_a2a_tool.py -q
```

Results: both ancestry checks succeeded; all four Go packages reported `ok`; Python reported `28 passed` (three dependency warnings).
