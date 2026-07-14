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

#### Scenario: Missing context behavior is verified
- **WHEN** the first A2A message does not include a context identifier
- **THEN** compatibility validation records whether kagent supplies one before actor creation or the request fails before bridge execution

### Requirement: Direct Actor-Local ACP Supervision
The bridge SHALL supervise the ACP child inside the actor and SHALL use direct stdio to `codex-acp` by default. Shim loopback SHALL remain rejected unless the design documents a concrete requirement.

#### Scenario: ACP child is actor-local
- **WHEN** the bridge starts the Codex ACP child
- **THEN** ACP JSON-RPC is exchanged over the child stdin/stdout stream without requiring an actor-local WebSocket loopback

### Requirement: Durable Cold-Boot Reconstruction
The runtime SHALL assume bridge and ACP child processes may restart between turns. Mutable state required for continuity SHALL live under `/data`.

#### Scenario: Durable state is persisted before prompt execution
- **WHEN** the bridge starts an ACP prompt
- **THEN** it first persists the context identifier, ACP session identifier, Codex thread identifier when available, workspace path, and active operation record under `/data`

#### Scenario: Resume claim is scoped by available Codex state
- **WHEN** an actor resumes after data-only suspension
- **THEN** validation proves workspace continuity and proves conversational continuity only if pinned Codex ACP can reload the prior thread from durable state

### Requirement: Prompt Lifecycle and Terminal Result
The bridge SHALL keep the outer A2A request open until the ACP prompt reaches a terminal stop reason, emit exactly one terminal A2A result, and SHALL NOT continue prompts as untracked background work after returning. The bridge SHALL support concurrent cancellation/control-plane requests while a prompt stream is active.

#### Scenario: Prompt completes before actor suspension
- **WHEN** an A2A message starts an ACP prompt
- **THEN** ACP updates are streamed as A2A events until one terminal ACP stop reason is observed and exactly one terminal A2A result is emitted before the response closes

### Requirement: A2A Surface and Readiness Contract
The runtime SHALL pin the A2A protocol methods required by direct SandboxAgent chat and parent Agent-tool delegation. Runtime readiness SHALL mean the A2A server is listening, the bridge state directory is usable, and the child executable is present; readiness SHALL NOT require provider credentials, model authentication, a model call, or a persistent running ACP child.

#### Scenario: Readiness is provider independent
- **WHEN** the runtime reports ready through `/.well-known/agent-card.json`
- **THEN** the A2A server is reachable and local bridge prerequisites are present without requiring valid Codex credentials or provider availability

#### Scenario: Required A2A methods are declared
- **WHEN** the runtime design is finalized
- **THEN** it identifies whether `message/send`, `message/stream`, `tasks/get`, and `tasks/cancel` are required or whether a narrower method set is sufficient

### Requirement: Duplicate Concurrency and Failure Semantics
The bridge SHALL allow at most one active prompt per A2A context. Concurrent prompts SHALL be rejected as busy; duplicate deliveries SHALL NOT create duplicate Codex turns; cancellation and child crashes SHALL produce one terminal outcome and clear active operation state safely.

#### Scenario: Concurrent prompt is rejected
- **WHEN** a second prompt arrives for a context with an active operation
- **THEN** the bridge returns a busy outcome and does not start another ACP prompt

#### Scenario: Duplicate completed prompt is idempotent
- **WHEN** a duplicate task or message identifier is delivered after its operation completed
- **THEN** the bridge returns or reconstructs the prior terminal result and does not create a second Codex turn

#### Scenario: Child crash clears active operation
- **WHEN** the ACP child exits during an active prompt
- **THEN** the bridge emits one terminal failure result and clears the active operation marker safely

### Requirement: Permission Request Policy
The first POC SHALL deny ACP permission requests explicitly and emit visible A2A output. Full mapping to A2A input-required/HITL SHALL be deferred.

#### Scenario: Permission request is denied explicitly
- **WHEN** the ACP child sends a permission request during a prompt
- **THEN** the bridge denies it, emits a visible A2A event or result, and does not hang waiting for human input

### Requirement: Model-Independent and Codex Validation Lanes
Validation SHALL include a fake ACP stdio child lane for deterministic lifecycle proof and a Codex ACP lane for real adapter authentication plus one bounded model turn.

#### Scenario: Fake ACP lane proves lifecycle without credentials
- **WHEN** model-independent validation runs
- **THEN** the same bridge drives a fake ACP child that supports initialize, session creation, prompt streaming, cancellation, one controlled failure, and workspace marker access

#### Scenario: Codex ACP lane proves real adapter path
- **WHEN** credentialed validation runs with runtime-only Codex credentials
- **THEN** the same bridge authenticates the real Codex ACP child and completes one bounded model turn without leaking credentials into images, logs, or evidence

### Requirement: Parent Agent Context Propagation Gate
Before implementation, compatibility validation SHALL determine whether two invocations of the same SandboxAgent tool from one parent Agent conversation deliver the same child A2A context identifier or create a new child context per invocation.

#### Scenario: Parent delegation context behavior is recorded
- **WHEN** a parent Agent invokes the same SandboxAgent tool twice from one conversation
- **THEN** compatibility validation records whether the child receives a stable A2A context and the design either relies on that stability or defines an explicit delegation/session correlation mechanism

### Requirement: Parent Agent Delegation Acceptance
The POC SHALL include a final acceptance path where a declarative coordinator Agent delegates to the BYO SandboxAgent as an Agent tool after direct runtime proof passes.

#### Scenario: Coordinator delegates to SandboxAgent tool
- **WHEN** direct SandboxAgent runtime proof has passed
- **THEN** validation runs a coordinator Agent that references the BYO SandboxAgent as an Agent tool and delegates one task to the session-isolated Codex specialist
