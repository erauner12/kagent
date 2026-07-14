## 1. Resolve prerequisites and runtime ownership

- [ ] 1.1 Confirm the branch stack includes the Codex ACP sandbox packaging branch/commit, or add that packaging as prerequisite work before this change.
- [ ] 1.2 Verify the selected kagent controller/chart build supports BYO SandboxAgents with explicit commands, record the expected missing-`contextId` failure before bridge execution, and audit routability of `tasks/resubscribe`, `tasks/get`, and `tasks/cancel` through the substrate session transport.
- [ ] 1.3 Confirm the pinned A2A method surface: direct SandboxAgent chat uses `message/stream`, parent Agent-tool delegation uses non-streaming `message/send`, and task-id-only control-plane methods are platform limitations unless later core routing changes are made.
- [ ] 1.4 Using an existing SandboxAgent or trivial echo BYO fixture, record child body `contextId` and lineage headers for Go and Python parent Agent paths across same-turn and cross-turn tool calls; choose the correlation mechanism needed for one parent conversation to map to one child coding session.
- [ ] 1.5 Run a bounded bridge-language spike comparing Python and Go, with TypeScript allowed only if a concrete exception is justified and accepted.
- [ ] 1.6 Choose the fork-owned bridge source location, runtime image name, Docker/build path, registry, architecture support, and digest pinning policy for `a2a-codex-sandbox`.
- [ ] 1.7 Verify pinned `codex-acp` version/source behavior for direct stdio, authentication, session creation/loading/resume via thread ID, disconnect cancellation, permission request shapes, and durable `CODEX_HOME` restart support.

## 2. Build the minimal A2A-to-ACP runtime

- [ ] 2.1 Create the fork-owned BYO runtime source under the selected Python or Go ownership path with an A2A server on port 80 and `/.well-known/agent-card.json` readiness that does not require provider credentials or model availability.
- [ ] 2.2 Implement direct stdio supervision for the configurable ACP child, using `codex-acp` for the real lane and a fake ACP child for deterministic tests.
- [ ] 2.3 Implement the one-context-one-actor-one-workspace-one-logical-coding-session mapping and persist current/prior ACP and Codex backend identifiers plus per-operation terminal records under `/data` before prompt execution.
- [ ] 2.4 Configure durable paths for `/data/workspace`, bridge session metadata, operation records, and Codex state such as `CODEX_HOME` when supported.
- [ ] 2.5 Keep the outer A2A response open until the ACP turn reaches a terminal stop reason, then emit exactly one terminal A2A result and close.
- [ ] 2.6 Implement terminal busy/rejected A2A task results, duplicate task/message idempotence, disconnect-as-cancel handling, child crash handling, and safe active-operation cleanup with exactly one terminal outcome across completion, disconnect, timeout, and crash races.
- [ ] 2.7 Implement the first POC permission policy: deny ACP permission requests explicitly, emit visible A2A output, and never wait indefinitely for human input.

## 3. Add image packaging examples and CI coverage

- [ ] 3.1 Add the `a2a-codex-sandbox` Docker target or image build path with pinned runtime dependencies and no baked credentials.
- [ ] 3.2 Add the BYO SandboxAgent example or e2e fixture using the digest-pinned `a2a-codex-sandbox` image and an explicit command.
- [ ] 3.3 Add the parent declarative coordinator Agent example or e2e fixture that references the BYO SandboxAgent as an Agent tool.
- [ ] 3.4 Add Makefile and CI image-build coverage appropriate for the selected packaging path.
- [ ] 3.5 Ensure runtime Secret references pass `OPENAI_API_KEY` or `CODEX_API_KEY` plus `NO_BROWSER=1` only at runtime and never through image layers or build arguments.

## 4. Validate bridge behavior and isolation

- [ ] 4.1 Add model-independent fake-ACP tests for initialize, session/new, optional session/load, prompt streaming, cancellation, controlled failure, and workspace marker read/write.
- [ ] 4.2 Add focused Python or Go unit/contract tests for identity mapping, session sequencing, terminal result uniqueness, busy rejection, disconnect/cancel races, duplicate prompt idempotence, child clean/fail exit, missing credentials, and failed initialization.
- [ ] 4.3 Add or extend kagent e2e coverage for the fake ACP runtime lane with two A2A contexts, distinct actors, isolated `/data/workspace` markers, immediate back-to-back turns to expose suspend races, suspend/resume, disconnect-as-cancel, session deletion, peer non-interference, and cleanup.
- [ ] 4.4 Add credentialed Codex ACP smoke coverage for runtime-only initialize/authentication, one bounded model turn, permission-deny behavior if triggered, and observed conversational-continuity limits after restart.
- [ ] 4.5 Add parent-Agent delegation coverage after direct SandboxAgent proof passes.
- [ ] 4.6 Capture redacted evidence with prerequisite Codex packaging commit, versions, image digests per actor, ActorTemplate/image-shape pinning behavior, registry/WorkerPool pull result, safe context/actor/session/turn correlation, proof classification, and cleanup status.

## 5. Update kagent-native docs

- [ ] 5.1 Add an EP-style design document under `design/` if this experiment is prepared for upstream review beyond private OpenSpec planning.
- [ ] 5.2 Add runtime image usage documentation near the selected Docker/runtime source.
- [ ] 5.3 Add example documentation near the SandboxAgent example or e2e fixture.
- [ ] 5.4 Avoid adding kagent-garden operator docs, Garden command matrices, rendered golden responsibilities, or tool-specific committed mirrors.

## 6. Finalize and verify

- [ ] 6.1 Run `openspec validate --changes prototype-session-isolated-acp-agent` for the fork-local OpenSpec package and remember that `validation.md` and `documentation-impact.md` are supplementary under the `spec-driven` schema.
- [ ] 6.2 Run the relevant kagent build, lint, unit, image-build, and e2e validation commands for the selected implementation path.
- [ ] 6.3 Record exact validation commands and classify each proof as structural, fake-runtime, credentialed-Codex, or parent-Agent delegation.
- [ ] 6.4 Decide whether this private OpenSpec package remains fork-only planning, is paired with an upstream-facing EP, or is removed before any upstream PR.
