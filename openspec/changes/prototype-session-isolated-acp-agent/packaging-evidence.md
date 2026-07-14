# PR 7 packaging evidence (tasks 3.1–3.5)

This record covers structural image packaging and fixtures only. It does not
claim a registry/WorkerPool pull, Kind execution, credentialed Codex turn, or
parent-Agent delegation acceptance.

## Resolved runtime dependencies

| Package | Resolved version | Reproducibility control |
|---|---:|---|
| `@agentclientprotocol/codex-acp` | `1.1.2` | exact direct dependency in `python/packages/a2a-codex-sandbox/image/package.json` |
| `@openai/codex` | `0.144.0` | npm override plus committed `package-lock.json` |
| Python runtime dependencies | `python/uv.lock` resolutions | `uv sync --frozen` |

The npm lock records registry URLs and integrity hashes for the adapter, Codex,
and all other resolved npm dependencies. The Dockerfile has no credential build
arguments or credential values. `OPENAI_API_KEY` or `CODEX_API_KEY` is injected
only at runtime; the fixture uses a Kubernetes `secretKeyRef`. `CODEX_HOME` and
`NO_BROWSER` are non-secret runtime configuration.

The fixture image reference contains an explicit digest sentinel rather than a
tag. Replace it with the published multi-architecture index digest before later
Kind validation. No deployed or pull proof is claimed by this PR.
