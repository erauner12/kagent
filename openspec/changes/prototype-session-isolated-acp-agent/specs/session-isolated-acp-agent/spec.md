## ADDED Requirements

### Requirement: Experimental Runtime Ownership
The change SHALL introduce a fork-owned experimental BYO SandboxAgent runtime for session-isolated ACP coding-agent proof. The runtime SHALL be distinct from `acp-sandbox-codex` and SHALL expose A2A on port 80 with `/.well-known/agent-card.json` readiness.

#### Scenario: BYO runtime is distinct from ACP sandbox image
- **WHEN** the session-isolated ACP agent fixture is rendered
- **THEN** the SandboxAgent workload uses the repo-owned A2A-facing runtime image rather than deploying `acp-sandbox-codex` directly

#### Scenario: Runtime satisfies SandboxAgent entrypoint contract
- **WHEN** the runtime ActorTemplate is created
- **THEN** it uses an explicit command and exposes the A2A agent-card readiness endpoint on port 80

### Requirement: Version and Artifact Pinning
The change SHALL record the prerequisite Codex ACP sandbox packaging branch or commit, and SHALL pin the kagent controller/chart build, Codex ACP package version, Codex ACP source or image commit, and BYO bridge image digest used for validation.

#### Scenario: Compatibility audit records runtime boundary
- **WHEN** validation runs against the POC
- **THEN** the evidence records the prerequisite Codex packaging branch or commit, exact kagent build, Codex ACP version/source, bridge image digest, registry location, required architecture, and WorkerPool image pull result

### Requirement: Session Identity Invariant
The bridge SHALL treat one A2A context as one Substrate actor, one durable workspace, and one logical coding session. ACP session IDs and Codex thread IDs SHALL be treated as backend mappings that may be rebound during reconstruction rather than as stable identity.

#### Scenario: Context maps to one actor workspace and logical session
- **WHEN** two distinct A2A contexts are sent to one BYO SandboxAgent
- **THEN** validation observes two distinct actors, two distinct `/data/workspace` locations, and separate logical coding-session mappings

#### Scenario: Deleting one session preserves peer isolation
- **WHEN** two A2A contexts have active or resumable actors and one session actor is deleted
- **THEN** the peer context remains usable and its `/data/workspace` marker is unchanged

#### Scenario: Missing context behavior is verified
- **WHEN** the first A2A message does not include a context identifier
- **THEN** compatibility validation records the expected substrate transport failure before bridge execution, including the deployed-build error shape

### Requirement: Direct Actor-Local ACP Supervision
The bridge SHALL supervise the ACP child inside the actor and SHALL use direct stdio to `codex-acp` by default. Shim loopback SHALL remain rejected unless the design documents a concrete requirement.

#### Scenario: ACP child is actor-local
- **WHEN** the bridge starts the Codex ACP child
- **THEN** ACP JSON-RPC is exchanged over the child stdin/stdout stream without requiring an actor-local WebSocket loopback

### Requirement: Durable Cold-Boot Reconstruction
The runtime SHALL assume bridge and ACP child processes may restart between turns. Mutable state required for continuity SHALL live under `/data`. Backend session identifiers SHALL be persisted with confidence state so stale load candidates can be invalidated without losing workspace continuity.

#### Scenario: Durable state is persisted before prompt execution
- **WHEN** the bridge starts an ACP prompt
- **THEN** it first persists the context identifier, ACP session identifier when available, Codex thread identifier when available, Codex rollout path when available, workspace path, session-ID confidence, and active operation record under `/data`

#### Scenario: Resume claim is scoped by available Codex state
- **WHEN** an actor resumes after data-only suspension
- **THEN** validation proves workspace continuity and proves conversational continuity only if pinned Codex ACP can reload the prior thread from durable state

#### Scenario: Stale load candidate falls back to new session
- **WHEN** the bridge attempts `session/load` with a persisted candidate ID and the ACP child reports a not-found or stale-session error
- **THEN** the bridge invalidates that candidate, records the fallback, creates a new ACP session, and does not claim conversational continuity for that resume

#### Scenario: Load replay updates are suppressed from live turn output
- **WHEN** `session/load` emits replayed `session/update` notifications before the new prompt starts
- **THEN** the bridge records diagnostics but does not stream those replayed historical updates as fresh A2A output

### Requirement: Prompt Lifecycle and Terminal Result
The bridge SHALL keep the outer A2A request open until the ACP prompt response reaches a terminal stop reason, emit exactly one terminal A2A result, and SHALL NOT continue prompts as untracked background work after returning. Terminal prompt status SHALL be keyed on the `session/prompt` response rather than inferred from intermediate updates. For this POC, cancellation SHALL be modeled as stream disconnect or response close rather than as routable `tasks/cancel`, because current substrate session routing keys on body `contextId` and current kagent clients do not issue `tasks/cancel`.

#### Scenario: Terminal result precedes actor suspension
- **WHEN** an A2A message starts an ACP prompt
- **THEN** ACP updates are streamed as A2A events until one terminal ACP stop reason is observed, exactly one terminal A2A result is emitted, and actor suspension occurs only after the response closes

#### Scenario: Stream disconnect cancels active prompt
- **WHEN** the outer A2A stream disconnects before the ACP prompt reaches a terminal stop reason
- **THEN** the bridge attempts ACP cancellation or child teardown, persists one terminal canceled or aborted operation record, and does not continue the prompt in the background

### Requirement: A2A Surface and Readiness Contract
The runtime SHALL pin the A2A protocol methods required by direct SandboxAgent chat and parent Agent-tool delegation. Direct chat SHALL use `message/stream`; parent Agent-tool delegation SHALL use non-streaming `message/send`. `tasks/resubscribe`, `tasks/get`, and `tasks/cancel` routability SHALL be recorded as platform audit findings rather than required runtime support in the first POC. Runtime readiness SHALL mean the A2A server is listening, the bridge state directory is usable, and the child executable is present; readiness SHALL NOT require provider credentials, model authentication, a model call, or a persistent running ACP child.

#### Scenario: Readiness is provider independent
- **WHEN** the runtime reports ready through `/.well-known/agent-card.json`
- **THEN** the A2A server is reachable and local bridge prerequisites are present without requiring valid Codex credentials or provider availability

#### Scenario: Required A2A methods are declared
- **WHEN** the runtime design is finalized
- **THEN** it records `message/stream` for direct chat, `message/send` for parent delegation, and the current platform limitation for task-id-only control-plane methods

### Requirement: Duplicate Concurrency and Failure Semantics
The bridge SHALL allow at most one active prompt per A2A context. Concurrent prompts SHALL be rejected as terminal busy/rejected A2A task results rather than HTTP transport failures; duplicate deliveries SHALL NOT create duplicate Codex turns; disconnect cancellation and child crashes SHALL produce one terminal outcome and clear active operation state safely. The persisted operation ledger SHALL be the bridge source of truth for active-operation reconciliation after interrupts, crashes, and cold boots.

#### Scenario: Concurrent prompt is rejected
- **WHEN** a second prompt arrives for a context with an active operation
- **THEN** the bridge returns a terminal busy/rejected A2A task result and does not start another ACP prompt

#### Scenario: Duplicate completed prompt is idempotent
- **WHEN** a duplicate task or message identifier is delivered after its operation completed
- **THEN** the bridge returns or reconstructs the prior terminal result from per-operation records and does not create a second Codex turn

#### Scenario: Child crash clears active operation
- **WHEN** the ACP child exits during an active prompt
- **THEN** the bridge emits one terminal failure result and clears the active operation marker safely

#### Scenario: Stale lifecycle generation cannot settle a newer operation
- **WHEN** a stale stream handler, cancel handler, or teardown path races with a newer operation generation
- **THEN** the stale generation cannot register a terminal outcome for the newer operation

### Requirement: Diagnostics and Timeout Policy
The bridge SHALL expose redacted diagnostics for ACP JSON-RPC traffic, child stderr, invalid JSON, unmatched response IDs, load fallback, replay suppression, and lifecycle generation transitions. Bootstrap and control-plane operations SHALL have finite timeouts; prompts SHALL be bounded by A2A stream lifetime and disconnect-as-cancel rather than an arbitrary prompt timeout.

#### Scenario: Diagnostics capture protocol faults without secrets
- **WHEN** the ACP child emits stderr, invalid JSON, or an unmatched response ID
- **THEN** the bridge records a redacted diagnostic event without credentials, full prompts, or workspace contents

#### Scenario: Bootstrap timeout is finite but prompt timeout is stream-bound
- **WHEN** initialize, session create/load, or authentication hangs
- **THEN** the bridge fails that control-plane operation with a bounded timeout, while active prompts remain bounded by stream disconnect or terminal prompt response

### Requirement: Permission Request Policy
The first POC SHALL deny ACP permission requests explicitly and emit visible A2A output. The bridge SHALL implement deny/cancel handling for the permission request shapes emitted by pinned `codex-acp`, including command execution, file changes, permission-profile requests, and MCP elicitation when observed. Full mapping to A2A input-required/HITL SHALL be deferred.

#### Scenario: Permission request is denied explicitly
- **WHEN** the ACP child sends a permission request during a prompt
- **THEN** the bridge denies it, emits a visible A2A event or result, and does not hang waiting for human input

### Requirement: Model-Independent and Codex Validation Lanes
Validation SHALL include a fake ACP stdio child lane for deterministic lifecycle proof and a Codex ACP lane for real adapter authentication plus one bounded model turn.

#### Scenario: Fake ACP lane proves lifecycle without credentials
- **WHEN** model-independent validation runs
- **THEN** the same bridge drives a fake ACP child that supports initialize, session creation, session/load replay, prompt streaming, cancellation settlement, one controlled failure, and workspace marker access

#### Scenario: Codex ACP lane proves real adapter path
- **WHEN** credentialed validation runs with runtime-only Codex credentials
- **THEN** the same bridge authenticates the real Codex ACP child and completes one bounded model turn without leaking credentials into images, logs, or evidence

### Requirement: Parent Agent Context Propagation Gate
Before parent-delegation continuity is claimed, compatibility validation SHALL record the parent runtime behavior and the implementation SHALL define an explicit correlation mechanism from parent conversation/root context to child SandboxAgent body `contextId` unless the selected runtime already provides that behavior. Current repo research expects Go parent tools to reuse one child context per parent pod/process and Python parent tools to create a fresh child context per turn.

#### Scenario: Parent delegation context behavior is recorded
- **WHEN** Go and Python parent Agent paths invoke the same SandboxAgent tool across same-turn and cross-turn calls
- **THEN** compatibility validation records the child body `contextId`, lineage headers, parent runtime, and selected correlation mechanism needed for one parent conversation to map to one child coding session

### Requirement: Parent Agent Delegation Acceptance
The POC SHALL include a final acceptance path where a declarative coordinator Agent delegates to the BYO SandboxAgent as an Agent tool after direct runtime proof passes.

#### Scenario: Coordinator delegates to SandboxAgent tool
- **WHEN** direct SandboxAgent runtime proof has passed
- **THEN** validation runs a coordinator Agent that references the BYO SandboxAgent as an Agent tool and delegates one task to the session-isolated Codex specialist
