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

The BYO SandboxAgent manifest must use an explicit command because the Substrate ActorTemplate path copies command and args directly. The runtime must listen on port 80 and expose the agent card readiness endpoint expected by the current SandboxAgent translation path. Initial readiness means the A2A server is listening, the bridge state directory is usable, and the child executable is present; it does not require valid OpenAI credentials, successful provider authentication, a model call, or a persistent running `codex-acp` child.

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

The bridge must persist `contextId`, stable actor/workspace identity, current ACP session ID when available, current Codex thread ID when available, prior ACP session IDs, workspace path, last completed operation, and active operation before calling ACP. The implementation must configure Codex state ownership, such as `CODEX_HOME`, when the pinned adapter/Codex runtime supports it.

Continuity claims are separate:

- workspace continuity: files under `/data/workspace` survive;
- bridge continuity: context/session/thread mappings can be reconstructed;
- coding-agent continuity: Codex thread/history can actually be resumed.

If pinned `codex-acp` cannot reload the prior thread after restart, the POC may still pass workspace continuity but must not claim conversational continuity.

### Session and operation mapping

The stable invariant is:

```text
one A2A contextId = one actor = one durable workspace = one logical coding session
```

A2A tasks/messages are turns inside that context, not independent coding sessions. ACP session IDs and Codex thread IDs are backend mappings that may be rebound during cold-boot reconstruction; they are observed state, not the stable identity contract. The compatibility audit must verify what happens when the first A2A message lacks a `contextId`; the POC must not assume clients always supply one.

The outer A2A response must stay open until the ACP prompt reaches a terminal stop reason. The bridge SHALL NOT continue a prompt as untracked background work after returning a terminal A2A response.

Before implementation, the design must pin the exact A2A protocol surface required by direct SandboxAgent chat and parent Agent-tool delegation, including whether `message/send`, `message/stream`, `tasks/get`, and `tasks/cancel` are all required or whether a narrower subset is sufficient.

A second concurrent prompt for the same context is rejected as busy, while concurrent control-plane requests such as `tasks/cancel` must still be served during an active stream. Duplicate delivery with the same A2A task/message identifier must not create a second Codex turn; completed duplicates return or reconstruct the prior terminal result. A canceled operation emits one terminal canceled result. A child crash emits one terminal failure and clears the active operation marker safely. Completion, cancel, timeout, and crash races must synchronize on the operation record so exactly one terminal outcome is emitted.

### Parent-Agent context propagation gate

Parent-Agent delegation is not just final acceptance. Before bridge implementation, the compatibility audit must invoke the same SandboxAgent tool twice from one parent-Agent conversation and record whether the child receives the same A2A `contextId`. If the child context is stable, the existing context-to-actor mapping is sufficient. If kagent creates a new child context per tool invocation, the bridge or caller needs an explicit delegation/session correlation mechanism before parent-Agent continuity claims can be made.

### Permission policy

The first POC denies ACP permission requests explicitly, emits visible A2A output, and continues or terminates according to the backend response. Full mapping to A2A input-required/HITL is deferred.

## Risks / Trade-offs

- Direct stdio is simpler but diverges from the controller-side ACP gateway topology described in upstream design notes. This is intentional for the actor-local POC.
- Python likely fits the BYO runtime lane, while Go better matches eventual core integration. TypeScript may fit the ACP ecosystem but conflicts with kagent's repository guidance unless explicitly accepted.
- Codex conversational continuity may not be available after cold boot. The POC must keep workspace continuity, bridge continuity, and conversational continuity as separate claims.
- Registry and egress assumptions can fail even if local Kind image loading works. WorkerPool pull and outbound Codex/OpenAI connectivity must be validated explicitly.

## Open Questions

- Which bridge language should be used for this repo-owned POC: Python for agent-runtime fit, Go for later kagent-core migration fit, or TypeScript only with an explicit exception?
- Does pinned `codex-acp` support reloading a prior thread/session after process restart from durable state?
- Which exact A2A protocol methods are required by direct SandboxAgent chat and parent Agent-tool delegation?
- Does parent-Agent tool delegation preserve the same child A2A context across repeated calls in one parent conversation, or create a new child context each time?
