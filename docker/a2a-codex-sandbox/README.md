# A2A Codex sandbox image

This image packages the `a2a-codex-sandbox` Python runtime with
`@agentclientprotocol/codex-acp@1.1.2`. The committed npm lockfile and override
resolve `@openai/codex` to `0.144.0`; Python dependencies come from
`python/uv.lock` with `uv sync --frozen`.

Build and push it using the repository image conventions:

```sh
make build-a2a-codex-sandbox
```

The image contains no provider credentials and accepts no credential build
arguments. At runtime it uses `CODEX_HOME=/data/codex`; inject `NO_BROWSER=1` and either `OPENAI_API_KEY` or `CODEX_API_KEY` from a Kubernetes Secret.

See `examples/session-isolated-acp-agent/` for the substrate BYO fixture. Its
digest sentinel must be replaced with the published multi-architecture index
digest before applying it.
