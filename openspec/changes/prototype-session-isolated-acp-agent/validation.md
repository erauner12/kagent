## Validation Scope

Validation must prove that a fork-owned BYO SandboxAgent runtime can bridge A2A requests to an ACP child while preserving the session isolation and lifecycle guarantees claimed by the spec.

The proof is split into two lanes:

- fake ACP lane: model-independent proof of bridge behavior, actor lifecycle, persistence, cancellation, and isolation;
- Codex ACP lane: credentialed proof that the same bridge can authenticate the real pinned Codex ACP child and complete one bounded model turn.

## Preconditions

- The prerequisite Codex ACP sandbox packaging branch or commit is present in the stack, or equivalent packaging has been added first.
- The selected kagent build and chart support BYO SandboxAgents with explicit commands.
- The bridge image is published to a registry the WorkerPool can pull by digest for the required architecture.
- Runtime-only Secret material is available for the Codex lane: `OPENAI_API_KEY` or `CODEX_API_KEY`, plus `NO_BROWSER=1` in the workload environment.
- Outbound connectivity needed by Codex/OpenAI is available from the WorkerPool runtime.

## Structural Checks

- `openspec validate --changes prototype-session-isolated-acp-agent`
- Verify the BYO SandboxAgent fixture renders an explicit command, port 80 agent-card readiness, runtime-only credential references, and a digest-pinned bridge image.
- Verify examples/e2e fixtures reference the fork-owned A2A-facing runtime image, not `acp-sandbox-codex` directly.
- Verify runtime readiness is provider-independent: A2A server listening, bridge state directory usable, and child executable present.
- Verify no source, examples, or tests deploy `acp-sandbox-codex` directly as the BYO SandboxAgent workload.
- Verify no credentials, full environment dumps, or sensitive prompt/workspace contents appear in checked-in files, rendered fixtures/manifests, logs, or evidence.

## Runtime / Proof Checks

### Fake ACP lane: one-button / no-human

Run the bridge against a fake ACP stdio child that deterministically supports initialize, session/new, session/load success, session/load not-found fallback, load replay updates, session/prompt, streamed text/tool-like updates, cancellation settlement, one controlled failure, protocol faults for diagnostics, and marker read/write in the working directory.

Required proof:

1. Create two A2A contexts against one BYO SandboxAgent.
2. Observe two distinct actors and two distinct `/data/workspace` locations.
3. Persist bridge mapping, session-ID confidence, backend identifiers, and active operation state before prompt execution.
4. Stream updates and exactly one terminal result for each context, keyed on the prompt response stop reason.
5. Reject a second concurrent prompt as a terminal busy/rejected A2A task result.
6. Treat duplicate task/message delivery idempotently from per-operation records.
7. Disconnect one active stream and verify ACP cancellation or child teardown plus one terminal canceled/aborted operation record after prompt settlement when available.
8. Simulate child crash and verify one terminal failure plus active-operation cleanup.
9. Exercise `session/load` success with replayed updates and verify replay suppression from live A2A output.
10. Exercise `session/load` not-found fallback to `session/new`, stale-ID invalidation, and evidence classification that conversational continuity was not proven for that resume.
11. Run immediate back-to-back turns to surface suspend/resume and stale-generation teardown races.
12. Suspend/resume one actor and prove workspace continuity from `/data`.
13. Delete one session actor and verify the peer session remains usable.
14. Capture redacted diagnostics for JSON-RPC traffic, child stderr, invalid JSON, unmatched responses, load fallback, replay suppression, and generation transitions.
15. Clean up all fixtures and report leaked actors/resources as failure.

### Codex ACP lane: credentialed / no-human when secrets are available

Run the same bridge against pinned `codex-acp` with runtime-only Codex credentials.

Required proof:

1. Initialize/authenticate the real Codex ACP child.
2. Create or load the logical ACP session for the A2A context.
3. Complete one bounded model turn.
4. Record whether Codex conversational continuity survives process restart from durable `/data` state, including session-ID confidence, rollout path when available, load fallback, and replay suppression observations.
5. Deny any ACP permission request explicitly and avoid hanging.

### Parent-Agent delegation lane: credentialed / may depend on model availability

Before implementation, use an existing SandboxAgent or trivial echo BYO fixture to record child body `contextId` and lineage headers for Go and Python parent Agent paths across same-turn and cross-turn calls. Evidence should confirm the expected current behavior—Go reuses a child context per parent pod/process and Python creates a fresh child context per turn—or document deployed-build differences. After direct SandboxAgent proof passes and an explicit correlation mechanism is selected, run a declarative coordinator Agent that references the BYO SandboxAgent as an Agent tool and delegates one task to the session-isolated Codex specialist.

## Evidence / Success Signals

Evidence must include:

- prerequisite Codex packaging branch or commit, kagent controller/chart build, and Substrate/WorkerPool identity;
- bridge image tag/digest, registry location, architecture, and WorkerPool pull result;
- Codex ACP package version and source/image commit inspected;
- safe correlation of A2A context, actor, workspace, logical ACP session, Codex thread when known, rollout path when known, operation IDs, and session-ID confidence;
- readiness, prompt, disconnect-as-cancel, load fallback, replay suppression, suspend/resume, delete, diagnostics, and cleanup outcomes;
- proof classification for each lane: structural, fake-runtime, credentialed-Codex, or parent-Agent delegation.

## Supplementary Artifact Note

This file is supplementary under the `spec-driven` schema; OpenSpec validation tracks proposal, design, specs, and tasks. Keep validation requirements mirrored in the tracked spec and tasks when they are normative.

## Out-of-Scope Claims

This validation does not prove production multi-tenancy, production security hardening, disaster recovery, generic ACP compatibility, full A2A HITL permission handling, autonomous coding quality, or persistent AgentHarness backend support.

If pinned Codex ACP cannot reload a prior thread after process restart, validation may still prove workspace continuity and bridge reconstruction, but it must not claim conversational continuity.
