# syntax=docker/dockerfile:1

ARG NODE_IMAGE=node:22-trixie-slim@sha256:e6d9a389d34ff9678438af985c9913fbd1eb6ed36e80fea56644f4b4f6dd70ba
ARG UV_VERSION=0.11.15

FROM ghcr.io/astral-sh/uv:${UV_VERSION}@sha256:e590846f4776907b254ac0f44b5b380347af5d90d668138ca7938d1b0c2f98d3 AS uv-bin

FROM ${NODE_IMAGE} AS python-builder
ARG TOOLS_PYTHON_VERSION=3.13

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_PROGRESS=1 \
    UV_PROJECT_ENVIRONMENT=/opt/a2a-codex-sandbox \
    UV_PYTHON_INSTALL_DIR=/python \
    UV_PYTHON_PREFERENCE=only-managed

COPY --from=uv-bin /uv /uvx /usr/local/bin/
WORKDIR /workspace
COPY pyproject.toml .python-version uv.lock README.md ./
COPY packages/a2a-codex-sandbox packages/a2a-codex-sandbox
RUN --mount=type=cache,target=/root/.cache/uv,rw \
    uv sync --frozen --package a2a-codex-sandbox --no-dev --no-editable

FROM ${NODE_IMAGE} AS node-deps
WORKDIR /opt/codex-acp
COPY packages/a2a-codex-sandbox/image/package.json packages/a2a-codex-sandbox/image/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm,rw npm ci --omit=dev

FROM ${NODE_IMAGE}
ARG VERSION

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system agent \
    && useradd --system --gid agent --home-dir /home/agent --create-home agent

COPY --from=python-builder /python /python
COPY --from=python-builder /opt/a2a-codex-sandbox /opt/a2a-codex-sandbox
COPY --from=node-deps /opt/codex-acp /opt/codex-acp

RUN ln -s /opt/codex-acp/node_modules/.bin/codex-acp /usr/local/bin/codex-acp \
    && ln -s /opt/codex-acp/node_modules/.bin/codex /usr/local/bin/codex \
    && mkdir -p /data/workspace /data/bridge /data/codex \
    && chown -R agent:agent /data

ENV A2A_DATA_DIR=/data \
    CODEX_HOME=/data/codex \
    PATH="/opt/a2a-codex-sandbox/bin:${PATH}" \
    PYTHONUNBUFFERED=1

USER agent
WORKDIR /data/workspace
EXPOSE 80

LABEL org.opencontainers.image.source="https://github.com/kagent-dev/kagent" \
      org.opencontainers.image.description="Session-isolated A2A to Codex ACP sandbox runtime" \
      org.opencontainers.image.version="${VERSION}" \
      dev.kagent.codex-acp.version="1.1.2" \
      dev.kagent.codex.version="0.144.0"

ENTRYPOINT ["/opt/a2a-codex-sandbox/bin/a2a-codex-sandbox"]
