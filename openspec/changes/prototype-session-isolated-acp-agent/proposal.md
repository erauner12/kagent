## Why

Codex ACP packaging is a prerequisite for this experiment, but an ACP sandbox image by itself does not satisfy kagent's BYO `SandboxAgent` contract. A BYO SandboxAgent workload must expose A2A over HTTP on port 80 and serve `/.well-known/agent-card.json`.

The key product question is whether kagent's existing one-session-to-one-actor `SandboxAgent` lifecycle can host a coding specialist that a normal Agent can delegate to while preserving session isolation, cancellation, cleanup, and reconstructable state across data-only resumes.

This OpenSpec package is fork-local planning for the `kagent` repo. It does not propose that upstream kagent adopt this OpenSpec scaffolding; an upstream-facing version should be a concise EP-style design document if maintainers want that lane. This POC is an actor-local complement to, not a replacement for, the controller-side ACP bridge direction described in `design/EP-XXXX-acp-integration.md`.

## What Changes

This change will define and implement a narrow experimental BYO runtime, tentatively `a2a-codex-sandbox`, owned by this `kagent` fork:

- an A2A server on port 80 with `/.well-known/agent-card.json` readiness;
- an A2A-to-ACP bridge that supervises a pinned `codex-acp` child over direct stdio by default;
- durable state under `/data` for workspace files, bridge session metadata, operation records, and Codex state;
- fake-ACP and Codex-ACP validation lanes for bridge/lifecycle proof and one bounded real-adapter turn;
- a BYO `SandboxAgent` example or e2e fixture and a parent declarative Agent delegation acceptance path.

This change depends on the Codex ACP sandbox packaging branch/commit being present in the stack, or on adding that packaging as a prerequisite first. Do not describe Codex packaging as landed unless the branch base contains it.

## Affected Owner Seams

- Runtime source: a new fork-owned BYO runtime under the selected Python or Go ownership path.
- Image packaging: a new Docker target or image build path for `a2a-codex-sandbox`, with pinned `codex-acp` dependency and immutable image digest for validation.
- Examples/tests: SandboxAgent example or e2e fixture, fake ACP protocol tests, Codex ACP smoke coverage, and parent-Agent delegation acceptance.
- CI/build: Makefile and GitHub workflow coverage for the runtime image and focused tests, as appropriate for the selected implementation path.
- Design docs: a kagent-native design note, likely under `design/`, if this becomes upstream-facing beyond private OpenSpec planning.

## Change Class

- Type: capability-introduction
- Mode: fork-local-design

## Capabilities

### New Capabilities

- `session-isolated-acp-agent`: Introduces an experimental BYO SandboxAgent runtime that bridges A2A requests to a Codex ACP child while preserving one A2A context to one actor/workspace/session mapping.

### Modified Capabilities

- None initially. Existing kagent AgentHarness behavior remains unchanged.

## Non-Goals

- No upstream controller, CRD, UI, or AgentHarness backend changes in this POC.
- No production-grade multi-tenancy, security hardening, or disaster-recovery claim.
- No direct deployment of `acp-sandbox-codex` as the BYO SandboxAgent workload.
- No generic ACP framework or Claude implementation.
- No persistent `backend: codex` AgentHarness baseline in this change; that remains a separate optional comparison branch.
- No full HITL permission mapping in the first POC; ACP permission requests are denied explicitly unless later design proves a safe supported mapping.
- No kagent-garden deployment, Garden commands, Kustomize ownership, or rendered-golden responsibilities in this repository change.

## Impact

- Adds a fork-owned experimental runtime image and validation fixtures.
- Adds kagent-native unit, protocol, and e2e coverage around the BYO runtime and SandboxAgent composition path.
- May add image build/release plumbing for the runtime image.
- May add an EP-style design document if this work is prepared for upstream review.
- Leaves kagent-garden consumption of a pinned artifact as a separate downstream change.
