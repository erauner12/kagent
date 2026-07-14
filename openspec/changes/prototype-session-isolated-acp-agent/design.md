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

The audit selects `python/packages/a2a-codex-sandbox/` for source, `docker/a2a-codex-sandbox/Dockerfile` plus root Make target `build-a2a-codex-sandbox` for packaging, and `ghcr.io/kagent-dev/kagent/a2a-codex-sandbox` for published images. Publish a Linux multi-architecture index for `linux/amd64` and `linux/arm64`; examples and evidence use its immutable digest and record per-platform digests. Tags are convenience only. Registry pull and architecture claims remain unproven until their later validation lanes pass.

The prerequisite packaging commit is `f7412069030dfef2a0b5df774c32ebb41c3f1398`, present in the selected kagent base `0e27e4665f0fa55936c8fa4842b323772a870e1e`. The audited adapter is `@agentclientprotocol/codex-acp@1.1.2` at source tag `8aff492d4b033ff2c02ad3b9d591994d57617463`. Evidence must additionally record the deployed controller/chart, bridge digest, registry/WorkerPool pull result, and resolved transitive Codex version when those artifacts exist.

### Runtime image shape

The POC introduces the distinct A2A-facing BYO runtime image `a2a-codex-sandbox`. It may reuse Node base stages, the pinned Codex ACP package, and hardening choices from ACP sandbox packaging, but it must run its own A2A server and cannot rely on the `acp-sandbox-codex` entrypoint.

The BYO SandboxAgent manifest must use an explicit command because the Substrate ActorTemplate path copies command and args directly. The runtime must listen on port 80 and expose the agent card readiness endpoint expected by the current SandboxAgent translation path. Initial readiness means the A2A server is listening, the bridge state directory is usable, and the child executable is present; it does not require valid OpenAI credentials, successful provider authentication, a model call, or a persistent running `codex-acp` child. This matters because the current SandboxAgent readiness path probes `/.well-known/agent-card.json` on port 80 during ActorTemplate/golden snapshot creation and resume; provider-dependent readiness would make model-independent snapshots fail when credentials are intentionally absent.

### Bridge topology

Default topology is direct stdio:

```text
A2A server / bridge → codex-acp stdin/stdout
```

`acp-shim` loopback is rejected unless implementation discovers a concrete requirement. In this actor-local topology the bridge is already next to the child process, so adding WebSocket loopback would add a listener, process, transport, and failure boundary without solving the original remote-stdio problem.

### Bridge implementation language

The bridge SHALL be implemented in Python. Both Python and Go have supported A2A server paths and require a similarly thin ACP NDJSON client; both can supervise a fake or real child with streaming and cancellation. Python wins because this is an experimental BYO/agent runtime under the repository's Python ownership boundary. Go remains appropriate only for a later controller-owned integration. TypeScript is rejected: the Node-based child does not require the bridge to share its language, and no repository-guidance exception is justified.

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

If pinned `codex-acp` cannot reload the prior thread after restart, the POC may still pass workspace continuity but must not claim conversational continuity. The selected adapter is 1.1.2: its ACP session ID is the Codex thread ID, `session/load` and `session/resume` call `thread/resume`, and load replays stored history as `session/update` notifications. It does not expose the rollout JSONL path, so that diagnostic is recorded as unavailable rather than inferred.

The adapter propagates the Codex App Server load error rather than normalizing a stable not-found type. For the pinned contract, fallback is limited to JSON-RPC code `-32600` with a message containing `thread not found: <id>` (or a later explicitly fixture-backed equivalent). Authentication, transport, and arbitrary load failures do not trigger fallback. On a matched stale load, create a new session, invalidate the candidate, record the fallback, and scope the claim to workspace/bridge continuity.

The adapter's in-memory ACP state does not survive restart. Start every child with `CODEX_HOME=/data/codex`, reinitialize, then load by persisted thread ID. Adapter 1.1.2 is retained for PR 2 unless a separately reviewed upgrade is required. Because its published dependency on `@openai/codex` is a range, packaging must also make the resolved Codex version reproducible; pinning only the adapter is insufficient.

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

Both runtimes propagate lineage headers such as `x-kagent-parent-context-id` and `x-kagent-root-context-id`, but the current substrate actor routing uses the body A2A `contextId`, not those headers. The selected mechanism is caller-side deterministic derivation of child body `contextId` from the root context ID plus remote SandboxAgent namespace/name, using a namespaced hash/UUID valid as an A2A ID. Missing root falls back to the immediate parent context; raw correlation inputs are not logged. Both Go and Python parent tools must use this derivation before parent-delegation acceptance. The bridge cannot repair this mapping because substrate routing occurs before bridge execution.

### Permission policy

The first POC denies ACP permission requests explicitly, emits visible A2A output, and continues or terminates according to the backend response. The bridge must implement ACP client-side handlers for the permission shapes emitted by pinned `codex-acp`—including command execution, file changes, permission-profile requests, and MCP elicitation if observed—and select the appropriate deny/cancel response for each. Full mapping to A2A input-required/HITL is deferred; Codex approval policy should be configured to minimize permission prompts where safe.

#### Why HITL mapping is deferred: the suspend-vs-blocked-turn mismatch

Deferral is not just scope trimming; there is a structural mismatch this POC should record rather than rediscover later. ACP `session/request_permission` is synchronous inside an active prompt: `codex-acp` blocks mid-turn until the client answers. Kagent's A2A HITL contract is a pause across turns: the child returns a terminal `input-required` response, the outer response closes, and the decision arrives later as a new message. On substrate those two contracts collide, because the session transport suspends the actor when the response body closes. Emitting `input-required` and closing would data-only suspend the actor, cold-kill the bridge and the `codex-acp` child, and destroy the Codex turn that is blocked waiting for the answer. Codex rollout persistence restores thread history on `session/load`, not a turn frozen mid-permission, so the pending request cannot be reconstructed on resume. This is exactly the property that lets kagent's own ADK agents survive HITL across suspend—the ADK persists the paused invocation in its durable session store—and Codex has no equivalent mid-turn durability.

Candidate strategies for a later POC, none selected here:

- Re-prompt on resume: persist the permission request and the user's decision, replay the turn after cold boot, and auto-answer the re-occurring request from the stored decision. Fragile—the replayed turn may not ask the same question, and approving a side-effecting action against a re-rolled turn has idempotency risk.
- Hold the A2A stream open across the approval: keeps the actor and child alive but violates the kagent HITL turn contract and pins a WorkerPool worker for human-scale wait times.
- Defer suspension while an approval is pending: a controller-side change to the substrate session transport, out of scope for this fork-local POC but a legitimate later core proposal alongside control-plane routability.

Note the parent-side chain already exists: both ADK remote A2A tools propagate a child's `input-required` up through `request_confirmation()` and forward the decision back down by task/context ID. Coordinator-level, between-turn gates (approve before delegating, approve before proceeding) therefore map cleanly onto existing kagent HITL machinery today, because the pause lands where suspend is already safe. The hard case is specifically the per-tool-call, mid-Codex-turn approval, which is what deny-all punts on.

## Risks / Trade-offs

- Direct stdio is simpler but diverges from the controller-side ACP gateway topology described in `design/EP-XXXX-acp-integration.md`. This is intentional for the actor-local POC.
- Python likely fits the BYO runtime lane, while Go better matches eventual core integration. TypeScript may fit the ACP ecosystem but conflicts with kagent's repository guidance unless explicitly accepted.
- Codex conversational continuity appears available for current inspected versions, but only if the pinned version still supports thread resume and `CODEX_HOME` is durable under `/data`. The POC must keep workspace continuity, bridge continuity, and conversational continuity as separate claims.
- Registry and egress assumptions can fail even if local Kind image loading works. WorkerPool pull and outbound Codex/OpenAI connectivity must be validated explicitly. Existing session actors are also pinned to their birth ActorTemplate/image shape, so image digest bumps during validation behave like blue/green fan-out for new sessions rather than in-place upgrades; evidence should record the digest used by each actor.

## Open Questions

- Should a later kagent core change make task-id-only control methods routable, after this POC proves the disconnect-as-cancel baseline?
- Which strategy should a later POC adopt for mapping mid-turn ACP permission requests onto A2A input-required given the suspend-vs-blocked-turn mismatch: re-prompt with stored decisions, holding the stream open, or controller-side suspension deferral?
