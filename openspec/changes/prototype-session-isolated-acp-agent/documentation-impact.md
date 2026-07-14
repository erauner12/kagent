## Summary

This fork-local OpenSpec package documents an experimental kagent runtime proposal. Upstream-facing documentation, if needed, should be a concise EP-style design document rather than assuming upstream kagent will adopt this OpenSpec scaffolding.

This file is supplementary under the `spec-driven` schema; OpenSpec validation tracks proposal, design, specs, and tasks.

## Authoritative OpenSpec Surfaces

- Adds private/fork-local OpenSpec authority under `openspec/changes/prototype-session-isolated-acp-agent/`.
- Adds a change-local delta spec at `specs/session-isolated-acp-agent/spec.md`.
- Does not modify any upstream-recognized OpenSpec current-state surface because kagent does not currently use this OpenSpec tree as its public proposal lane.

## Support Docs

Implementation may add or update:

- `design/EP-XXXX-session-isolated-acp-agent.md` or equivalent, if the experiment is prepared for upstream review.
- Runtime image usage docs near the selected Docker/runtime source.
- Example documentation near the SandboxAgent example or e2e fixture.

## Operator Docs

No kagent-garden operator docs are owned by this change. Kagent-native user docs should be updated only if the runtime becomes a supported example or feature.

Possible future kagent docs:

- `examples/...` README for the experimental SandboxAgent runtime.
- `docs/...` only if maintainers accept the behavior as supported.
- release notes only if the runtime ships as a real feature.

## Generated / Mirrored Workflow Assets

No Garden/Kustomize rendered goldens are owned by this repo change. Generated assets are limited to normal kagent outputs if implementation touches CRDs, generated clients, or test fixtures.

## Legacy / Retired Lane Handling

The earlier `kagent-garden` planning document remains an external planning reference and is not archived by this kagent branch.

## Intentionally Unchanged Surfaces

- No kagent-garden Garden commands, operator matrix, operator-surface index, or rendered truth.
- No tool-specific committed mirrors under `.claude`, `.cursor`, `.codex`, or similar paths.
- No upstream kagent public proposal process change unless maintainers request it.
