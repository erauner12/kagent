## Context

The Codex ACP image work proves packaging for `codex-acp`, but `acp-sandbox-codex` exposes ACP over a shim and does not satisfy the BYO `SandboxAgent` contract. A BYO SandboxAgent runtime must expose A2A over HTTP on port 80 and serve `/.well-known/agent-card.json`.

Kagent already has the desired outer lifecycle: A2A `contextId` routes to a deterministic SandboxAgent actor, and that actor can be suspended, resumed, or deleted per session. The POC should use that existing seam rather than redesigning AgentHarness. AgentHarness remains a useful later comparison, but its current one-harness-one-actor behavior is not the isolation shape being tested here.

This change depends on the Codex ACP sandbox packaging branch/commit being present in the stack, or on adding that packaging as prerequisite work first. It also depends on confirming that this fork's selected controller/chart build supports BYO SandboxAgents with explicit commands; current source inspection alone is not enough.

## Goals / Non-Goals

**Goals:**

- Add a fork-owned experimental BYO runtime image, tentatively `a2a-codex-sandbox`.
- Prove one A2A context maps to one actor, one workspace, one logical ACP session, and one Codex thread unless evidence forces a narrower claim.
- Use direct stdio supervision of a pinned `codex-acp` child by default.
- Persist all bridge and workspace state needed for cold-boot reconstruction under `/data`.
- Validate with both a fake ACP child and the real Codex ACP adapter.
- Prove parent declarative Agent delegation to the BYO SandboxAgent after direct runtime proof passes.

**Non-Goals:**

- No upstream kagent controller, CRD, UI, or AgentHarness backend changes.
- No direct use of `acp-sandbox-codex` as the BYO SandboxAgent workload.
- No full A2A HITL permission mapping in the first POC.
- No production multi-tenancy, disaster recovery, or generic ACP compatibility claim.
- No persistent Codex AgentHarness baseline in this change.

## Decisions

### Source and artifact ownership

This `kagent` fork owns the first POC runtime source, image, examples/e2e fixtures, build integration, and tests. `kagent-garden` may later consume a pinned image and run local environment proof, but it is not an owner of this repository change.

The design must record:

- prerequisite Codex ACP sandbox packaging branch/commit or equivalent base commit;
- exact kagent commit or release and chart/controller image used for e2e validation;
- Codex ACP package version and source commit inspected;
- bridge image tag and digest;
- registry location, pull access, required architectures, and WorkerPool pull proof.

### Runtime image shape

The POC introduces a distinct A2A-facing BYO runtime image, tentatively `a2a-codex-sandbox`. It may reuse Node base stages, the pinned Codex ACP package, and hardening choices from ACP sandbox packaging, but it must run its own A2A server and cannot rely on the `acp-sandbox-codex` entrypoint.

The BYO SandboxAgent manifest must use an explicit command because the Substrate ActorTemplate path copies command and args directly. The runtime must listen on port 80 and expose the agent card readiness endpoint expected by the current SandboxAgent translation path. Initial readiness means the A2A server is listening, the bridge state directory is usable, and the child executable is present; it does not require valid OpenAI credentials, successful provider authentication, a model call, or a persistent running `codex-acp` child. This matters because the current SandboxAgent readiness path probes `/.well-known/agent-card.json` on port 80 during ActorTemplate/golden snapshot creation and resume; provider-dependent readiness would make model-independent snapshots fail when credentials are intentionally absent.

### Bridge topology

Default topology is direct stdio:

```text
A2A server / bridge → codex-acp stdin/stdout
```

`acp-shim` loopback is rejected unless implementation discovers a concrete requirement. In this actor-local topology the bridge is already next to the child process, so adding WebSocket loopback would add a listener, process, transport, and failure boundary without solving the original remote-stdio problem.

### Bridge implementation language

Language remains the first bounded spike, but it must follow kagent repository conventions. Python is the default fit for an experimental agent/BYO runtime. Go is the default fit if the implementation is intended to migrate into controller-owned kagent core. TypeScript should be used only with a concrete maintainer-accepted exception, even though the child `codex-acp` process is Node-based.

The decision must compare A2A server support, ACP client support, child-process supervision, streaming and cancellation, fake-child testability, existing kagent package boundaries, and likely reuse path.

### Persistence and restart model

The baseline lifecycle assumption is cold boot after data-only suspend/resume:

```text
actor identity survives
/data survives
bridge process may restart
codex-acp process may restart
ACP in-memory session may be gone
```

All mutable durable state must live under `/data`, for example:

```text
/data/workspace/
/data/bridge/session.json
/data/bridge/operations/
/data/codex/
```

The bridge must persist `contextId`, stable actor/workspace identity, current ACP session ID when available, current Codex thread ID when available, Codex rollout path when available, prior ACP session IDs, workspace path, last completed operation, and active operation before calling ACP. Persist both runtime-issued session IDs and load/resume candidate IDs with confidence states such as unavailable, candidate, and verified; mark an ID verified only after a successful `session/load` or equivalent resume. The implementation must configure Codex state ownership, such as `CODEX_HOME`, when the pinned adapter/Codex runtime supports it. Current repo research indicates `codex-acp` supports `session/load` and `session/resume` via Codex thread resume, and Codex stores resumable thread rollout JSONL under its home directory. The implementation must verify that behavior for the pinned version, persist the rollout path as a resume diagnostic/fallback when exposed, and place `CODEX_HOME` under `/data` before claiming conversational continuity.

Continuity claims are separate:

- workspace continuity: files under `/data/workspace` survive;
- bridge continuity: context/session/thread mappings can be reconstructed;
- coding-agent continuity: Codex thread/history can actually be resumed.

If pinned `codex-acp` cannot reload the prior thread after restart, the POC may still pass workspace continuity but must not claim conversational continuity. If `session/load` fails with a not-found or stale-session-shaped error, the bridge should fall back to `session/new`, invalidate the stale load candidate, record the fallback in evidence, and scope any resulting claim to workspace/bridge continuity rather than conversational continuity.

### Session and operation mapping

The stable invariant is:

```text
one A2A contextId = one actor = one durable workspace = one logical coding session
```

A2A tasks/messages are turns inside that context, not independent coding sessions. ACP session IDs and Codex thread IDs are backend mappings that may be rebound during cold-boot reconstruction; they are observed state, not the stable identity contract. The compatibility audit must verify what happens when the first A2A message lacks a `contextId`; the POC must not assume clients always supply one.

The outer A2A response must stay open until the ACP `session/prompt` response settles with a terminal stop reason. Terminal status must be keyed on the prompt response, not inferred from intermediate `session/update` events. The bridge SHALL NOT continue a prompt as untracked background work after returning a terminal A2A response.

The pinned method surface is intentionally narrow. Direct SandboxAgent chat uses `message/stream`. Parent Agent-tool delegation uses non-streaming `message/send`. Page reload may attempt `tasks/resubscribe`, but current substrate session routing expects a body `contextId` and `tasks/resubscribe`, `tasks/get`, and `tasks/cancel` carry task IDs instead. Treat those control-plane methods as platform audit findings, not requirements for the first bridge.

Platform-consistent cancellation for this POC is disconnect-as-cancel. If the outer stream disconnects or closes before a terminal ACP stop reason, the bridge treats the active operation as canceled/aborted, attempts to send ACP cancellation or tears down the child, waits for the in-flight prompt to settle when possible, persists exactly one terminal canceled/aborted operation record, and does not continue work in the background. The no-background rule is load-bearing: the current substrate transport suspends the session actor when the response body closes, so background work would be checkpointed or killed without a tracked terminal result.

A second concurrent prompt for the same context is rejected as a terminal busy/rejected A2A task result rather than a transport-level HTTP failure. Duplicate delivery with the same A2A task/message identifier must not create a second Codex turn; completed duplicates return or reconstruct the prior terminal result from per-operation records under `/data/bridge/operations/`. Because ACP does not provide an authoritative active-state snapshot comparable to first-party Codex protocols, the bridge operation ledger is the source of truth for interrupt, crash, and cold-boot reconciliation. A child crash emits one terminal failure and clears the active operation marker safely. Completion, disconnect, timeout, and crash races must synchronize on the operation record so exactly one terminal outcome is emitted; generation-counted lifecycle ownership should prevent stale teardown or stale stream handlers from completing a newer operation.


### ACP load replay and diagnostics

Some ACP providers replay prior conversation history as `session/update` notifications during `session/load`. The bridge must suppress load-replay updates from the live A2A stream for the new turn while still recording diagnostics, otherwise a cold-boot resume would re-emit old conversation events as fresh output. The fake ACP child should model replay updates during load so this behavior is tested without credentials.

Bootstrap and control-plane operations such as initialize, session creation, session load, and authentication should have finite timeouts to avoid wedged child processes. The prompt itself should not use an arbitrary wall-clock timeout; it is bounded by the A2A response lifetime and disconnect-as-cancel semantics.

The bridge should emit redacted diagnostics for outbound/inbound ACP JSON-RPC envelopes, child stderr, invalid JSON, unmatched response IDs, load fallback, replay suppression, and lifecycle generation transitions. Diagnostics are evidence plumbing and must not log secrets, full prompts, or workspace contents.

### Parent-Agent context propagation gate

Parent-Agent delegation is not just final acceptance. Repo research shows the current runtimes do not naturally provide the desired mapping of one parent conversation to one child coding session. The Go parent Agent path constructs the remote A2A tool once per parent pod/process and reuses one generated child context across conversations and users until restart. The Python parent Agent path rebuilds runner/tool objects per request, so repeated turns of one parent conversation receive fresh child contexts and therefore fresh actors.

Both runtimes propagate lineage headers such as `x-kagent-parent-context-id` and `x-kagent-root-context-id`, but the current substrate actor routing uses the body A2A `contextId`, not those headers. Before claiming parent-Agent continuity, the implementation must choose and validate an explicit correlation mechanism: for example, derive the child body `contextId` deterministically from the parent/root context at the caller, or add controller-side correlation in a later core change. The implementation audit should still confirm the observed per-runtime behavior against the exact selected build.

### Permission policy

The first POC denies ACP permission requests explicitly, emits visible A2A output, and continues or terminates according to the backend response. The bridge must implement ACP client-side handlers for the permission shapes emitted by pinned `codex-acp`—including command execution, file changes, permission-profile requests, and MCP elicitation if observed—and select the appropriate deny/cancel response for each. Full mapping to A2A input-required/HITL is deferred; Codex approval policy should be configured to minimize permission prompts where safe.

## Risks / Trade-offs

- Direct stdio is simpler but diverges from the controller-side ACP gateway topology described in `design/EP-XXXX-acp-integration.md`. This is intentional for the actor-local POC.
- Python likely fits the BYO runtime lane, while Go better matches eventual core integration. TypeScript may fit the ACP ecosystem but conflicts with kagent's repository guidance unless explicitly accepted.
- Codex conversational continuity appears available for current inspected versions, but only if the pinned version still supports thread resume and `CODEX_HOME` is durable under `/data`. The POC must keep workspace continuity, bridge continuity, and conversational continuity as separate claims.
- Registry and egress assumptions can fail even if local Kind image loading works. WorkerPool pull and outbound Codex/OpenAI connectivity must be validated explicitly. Existing session actors are also pinned to their birth ActorTemplate/image shape, so image digest bumps during validation behave like blue/green fan-out for new sessions rather than in-place upgrades; evidence should record the digest used by each actor.

## Open Questions

- Which bridge language should be used for this repo-owned POC: Python for agent-runtime fit, Go for later kagent-core migration fit, or TypeScript only with an explicit exception?
- Which explicit correlation mechanism should map a parent Agent conversation/root context to a child SandboxAgent body `contextId` for delegation continuity?
- Should platform control-plane methods such as `tasks/resubscribe`, `tasks/get`, and `tasks/cancel` be made routable in a later kagent core change, or should this POC rely only on disconnect-as-cancel and fresh task results?
