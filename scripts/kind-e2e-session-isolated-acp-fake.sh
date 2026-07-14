#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KIND_CLUSTER_NAME="${KIND_CLUSTER_NAME:-kagent}"
KUBE_CONTEXT="${KUBE_CONTEXT:-kind-${KIND_CLUSTER_NAME}}"
NAMESPACE="${NAMESPACE:-kagent}"
AGENT_NAME="${AGENT_NAME:-a2a-codex-sandbox-fake}"
WORKER_POOL="${WORKER_POOL:-kagent-default}"
CONTAINER_RUNTIME="${CONTAINER_RUNTIME:-docker}"
IMAGE_TAG="${A2A_CODEX_SANDBOX_FAKE_IMAGE:-localhost:5001/kagent-dev/kagent/a2a-codex-sandbox:fake-kind-e2e}"
FIXTURE="${FIXTURE:-${ROOT_DIR}/examples/session-isolated-acp-agent/sandbox-agent-fake-kind.yaml}"
EVIDENCE_FILE="${EVIDENCE_FILE:-/tmp/session-isolated-acp-fake-kind-evidence.md}"
USER_ID="${KAGENT_E2E_USER_ID:-fake-acp-kind-e2e@local}"
KAGENT_URL="${KAGENT_URL:-http://127.0.0.1:8083}"
TMP_DIR="$(mktemp -d)"
PORT_FORWARD_PID=""
APPLIED=false
SESSION_A=""
SESSION_B=""

log() { printf '==> %s\n' "$*"; }
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

cleanup() {
  local rc=$?
  if [[ "$APPLIED" == true ]]; then
    for session in "$SESSION_A" "$SESSION_B"; do
      [[ -z "$session" ]] || curl -fsS -X DELETE -H "X-User-Id: ${USER_ID}" \
        "${KAGENT_URL}/api/sessions/${session}" >/dev/null 2>&1 || true
    done
    kubectl --context "$KUBE_CONTEXT" -n "$NAMESPACE" delete -f "$TMP_DIR/fixture.yaml" \
      --ignore-not-found --wait=false >/dev/null 2>&1 || true
  fi
  [[ -z "$PORT_FORWARD_PID" ]] || kill "$PORT_FORWARD_PID" >/dev/null 2>&1 || true
  rm -rf "$TMP_DIR"
  exit "$rc"
}
trap cleanup EXIT

for command in "$CONTAINER_RUNTIME" kubectl curl jq sed; do
  command -v "$command" >/dev/null || fail "required command not found: $command"
done

kubectl --context "$KUBE_CONTEXT" get crd sandboxagents.kagent.dev >/dev/null
kubectl --context "$KUBE_CONTEXT" get crd actortemplates.ate.dev >/dev/null
kubectl --context "$KUBE_CONTEXT" -n "$NAMESPACE" get workerpool "$WORKER_POOL" >/dev/null
kubectl --context "$KUBE_CONTEXT" -n "$NAMESPACE" get deployment kagent-controller >/dev/null

log "building and loading ${IMAGE_TAG}"
"$CONTAINER_RUNTIME" buildx build \
  --load --provenance=false \
  --platform "linux/$(uname -m | sed 's/x86_64/amd64/; s/aarch64/arm64/')" \
  -t "$IMAGE_TAG" \
  -f "$ROOT_DIR/docker/a2a-codex-sandbox/Dockerfile" \
  "$ROOT_DIR/python"

log "pushing image to the Kind-local registry for WorkerPool pull"
"$CONTAINER_RUNTIME" push "$IMAGE_TAG" >/dev/null
IMAGE_REF="$("$CONTAINER_RUNTIME" image inspect "$IMAGE_TAG" \
  --format '{{range .RepoDigests}}{{println .}}{{end}}' | grep "^${IMAGE_TAG%:*}@" | head -1)"
[[ "$IMAGE_REF" == *@sha256:* ]] || fail "could not resolve pushed image digest for ${IMAGE_TAG}"
IMAGE_ID="$("$CONTAINER_RUNTIME" image inspect "$IMAGE_TAG" --format '{{.Id}}')"

sed "s|A2A_CODEX_SANDBOX_IMAGE_REF|${IMAGE_REF}|g" "$FIXTURE" > "$TMP_DIR/fixture.yaml"
grep -F "cmd: /opt/a2a-codex-sandbox/bin/a2a-codex-sandbox" "$TMP_DIR/fixture.yaml" >/dev/null
grep -F "value: /opt/a2a-codex-sandbox/bin/fake-acp-child" "$TMP_DIR/fixture.yaml" >/dev/null
grep -F 'value: "false"' "$TMP_DIR/fixture.yaml" >/dev/null

log "deploying fake ACP SandboxAgent"
kubectl --context "$KUBE_CONTEXT" apply -f "$TMP_DIR/fixture.yaml" >/dev/null
APPLIED=true
kubectl --context "$KUBE_CONTEXT" -n "$NAMESPACE" wait \
  --for=condition=Ready "sandboxagent/${AGENT_NAME}" --timeout=15m

TEMPLATE_ID="$(kubectl --context "$KUBE_CONTEXT" -n "$NAMESPACE" get actortemplates \
  -l "kagent.dev/sandbox-agent=${AGENT_NAME}" -o json | \
  jq -r '[.items[] | select(.status.phase == "Ready")] | sort_by(.metadata.creationTimestamp) | last | .metadata.name // empty')"
[[ -n "$TEMPLATE_ID" ]] || fail "no Ready ActorTemplate found"
TEMPLATE_IMAGE="$(kubectl --context "$KUBE_CONTEXT" -n "$NAMESPACE" get actortemplate "$TEMPLATE_ID" \
  -o json | jq -r '.spec.containers[0].image')"
[[ "$TEMPLATE_IMAGE" == "$IMAGE_REF" ]] || fail "ActorTemplate image mismatch: ${TEMPLATE_IMAGE}"

if ! curl -fsS "${KAGENT_URL}/health" >/dev/null 2>&1; then
  log "starting controller port-forward"
  kubectl --context "$KUBE_CONTEXT" -n "$NAMESPACE" port-forward deployment/kagent-controller 8083:8083 \
    >"$TMP_DIR/port-forward.log" 2>&1 &
  PORT_FORWARD_PID=$!
  for _ in {1..60}; do
    curl -fsS "${KAGENT_URL}/health" >/dev/null 2>&1 && break
    sleep 1
  done
fi
curl -fsS "${KAGENT_URL}/health" >/dev/null || fail "controller is not reachable at ${KAGENT_URL}"

A2A_URL="${KAGENT_URL}/api/a2a-sandboxes/${NAMESPACE}/${AGENT_NAME}"
STATUS_URL="${KAGENT_URL}/api/substrate/status?namespace=${NAMESPACE}"
RUN_ID="$(date -u +%Y%m%d%H%M%S)"
SESSION_A="fake-a-${RUN_ID}"
SESSION_B="fake-b-${RUN_ID}"

payload() {
  local context_id=$1 operation_id=$2 prompt=$3
  jq -nc --arg context "$context_id" --arg operation "$operation_id" --arg prompt "$prompt" '{
    jsonrpc:"2.0", id:$operation, method:"message/stream",
    params:{message:{kind:"message", role:"user", messageId:$operation, contextId:$context,
      parts:[{kind:"text", text:$prompt}]}}
  }'
}

send_stream() {
  local context_id=$1 operation_id=$2 prompt=$3 output=$4
  curl -fsS -N --max-time 180 -H 'Content-Type: application/json' -H "X-User-Id: ${USER_ID}" \
    --data "$(payload "$context_id" "$operation_id" "$prompt")" "$A2A_URL" | \
    sed -n 's/^data: //p' > "$output"
}

assert_completed_stream() {
  local file=$1 terminals artifacts state last_kind
  terminals="$(jq -s '[.[] | select(.result.final == true)] | length' "$file")"
  artifacts="$(jq -s '[.[] | select(.result.kind == "artifact-update")] | length' "$file")"
  state="$(jq -sr '[.[] | select(.result.final == true)] | last | .result.status.state // empty' "$file")"
  last_kind="$(tail -1 "$file" | jq -r '.result.kind // empty')"
  [[ "$terminals" == 1 ]] || fail "expected exactly one terminal result, got ${terminals}"
  [[ "$artifacts" -ge 2 ]] || fail "expected streamed prompt updates, got ${artifacts}"
  [[ "$state" == completed ]] || fail "expected completed terminal state, got ${state}"
  [[ "$last_kind" == status-update ]] || fail "terminal result was not the final stream event"
}

stream_text() {
  jq -sr '[.[] | select(.result.kind == "artifact-update") | .result.artifact.parts[]?.text // empty] | join("")' "$1"
}

substrate_status() {
  curl -fsS -H "X-User-Id: ${USER_ID}" "$STATUS_URL" | jq '.data'
}

actor_for_session() {
  substrate_status | jq -r --arg session "$1" --arg template "$TEMPLATE_ID" --arg namespace "$NAMESPACE" '
    .actors[] | select(.atespace == $namespace and .actorTemplateName == $template and (.actorId | contains($session))) |
    .actorId' | head -1
}

actor_state() {
  substrate_status | jq -r --arg actor "$1" '.actors[] | select(.actorId == $actor) | .status' | head -1
}

wait_actor_state() {
  local actor=$1 want=$2 state=""
  for _ in {1..180}; do
    state="$(actor_state "$actor")"
    [[ "$state" =~ $want ]] && return 0
    sleep 1
  done
  fail "actor ${actor} did not reach state matching ${want}; last state: ${state}"
}

wait_actor_absent() {
  local actor=$1
  for _ in {1..120}; do
    [[ -z "$(actor_state "$actor")" ]] && return 0
    sleep 1
  done
  fail "actor ${actor} still exists"
}

log "context A: marker write and immediate back-to-back read"
send_stream "$SESSION_A" "a-write-${RUN_ID}" "marker-write:actor-a" "$TMP_DIR/a-write.jsonl"
assert_completed_stream "$TMP_DIR/a-write.jsonl"
grep -F "marker:actor-a" <<<"$(stream_text "$TMP_DIR/a-write.jsonl")" >/dev/null || fail "context A marker write was not observed"
send_stream "$SESSION_A" "a-back-to-back-${RUN_ID}" "marker-read" "$TMP_DIR/a-back-to-back.jsonl"
assert_completed_stream "$TMP_DIR/a-back-to-back.jsonl"
grep -F "marker:actor-a" <<<"$(stream_text "$TMP_DIR/a-back-to-back.jsonl")" >/dev/null || fail "immediate back-to-back marker continuity failed"

ACTOR_A="$(actor_for_session "$SESSION_A")"
[[ -n "$ACTOR_A" ]] || fail "context A actor not found"
wait_actor_state "$ACTOR_A" '^(Suspended|Paused)$'

log "context A: resume and capture logical-session evidence"
send_stream "$SESSION_A" "a-resume-${RUN_ID}" "evidence" "$TMP_DIR/a-resume.jsonl"
assert_completed_stream "$TMP_DIR/a-resume.jsonl"
TEXT_A="$(stream_text "$TMP_DIR/a-resume.jsonl")"
WORKSPACE_A="$(sed -n 's/.*"workspace": "\([^"]*\)".*/\1/p' <<<"$TEXT_A" | head -1)"
ACP_SESSION_A="$(sed -n 's/.*"acpSessionId": "\([^"]*\)".*/\1/p' <<<"$TEXT_A" | head -1)"
[[ "$WORKSPACE_A" == /data/workspace ]] || fail "unexpected context A workspace: ${WORKSPACE_A}"
[[ -n "$ACP_SESSION_A" ]] || fail "context A ACP session evidence missing"

log "context B: distinct actor and isolated marker"
send_stream "$SESSION_B" "b-write-${RUN_ID}" "marker-write:actor-b" "$TMP_DIR/b-write.jsonl"
assert_completed_stream "$TMP_DIR/b-write.jsonl"
ACTOR_B="$(actor_for_session "$SESSION_B")"
[[ -n "$ACTOR_B" && "$ACTOR_B" != "$ACTOR_A" ]] || fail "contexts did not map to distinct actors"
wait_actor_state "$ACTOR_B" '^(Suspended|Paused)$'
send_stream "$SESSION_B" "b-evidence-${RUN_ID}" "evidence" "$TMP_DIR/b-evidence.jsonl"
assert_completed_stream "$TMP_DIR/b-evidence.jsonl"
TEXT_B="$(stream_text "$TMP_DIR/b-evidence.jsonl")"
WORKSPACE_B="$(sed -n 's/.*"workspace": "\([^"]*\)".*/\1/p' <<<"$TEXT_B" | head -1)"
ACP_SESSION_B="$(sed -n 's/.*"acpSessionId": "\([^"]*\)".*/\1/p' <<<"$TEXT_B" | head -1)"
[[ "$WORKSPACE_B" == /data/workspace ]] || fail "unexpected context B workspace: ${WORKSPACE_B}"
[[ -n "$ACP_SESSION_B" ]] || fail "context B ACP session evidence missing"
[[ "$ACTOR_A/$ACP_SESSION_A" != "$ACTOR_B/$ACP_SESSION_B" ]] || fail "contexts did not map to distinct actor-scoped ACP sessions"

send_stream "$SESSION_A" "a-isolation-${RUN_ID}" "marker-read" "$TMP_DIR/a-isolation.jsonl"
assert_completed_stream "$TMP_DIR/a-isolation.jsonl"
TEXT_A_MARKER="$(stream_text "$TMP_DIR/a-isolation.jsonl")"
grep -F "marker:actor-a" <<<"$TEXT_A_MARKER" >/dev/null || fail "context A marker changed"
! grep -F "marker:actor-b" <<<"$TEXT_A_MARKER" >/dev/null || fail "context A observed context B marker"
send_stream "$SESSION_B" "b-isolation-${RUN_ID}" "marker-read" "$TMP_DIR/b-isolation.jsonl"
assert_completed_stream "$TMP_DIR/b-isolation.jsonl"
TEXT_B_MARKER="$(stream_text "$TMP_DIR/b-isolation.jsonl")"
grep -F "marker:actor-b" <<<"$TEXT_B_MARKER" >/dev/null || fail "context B marker changed"
! grep -F "marker:actor-a" <<<"$TEXT_B_MARKER" >/dev/null || fail "context B observed context A marker"

log "disconnecting an active context A stream and verifying canceled duplicate replay"
CANCEL_OP="a-cancel-${RUN_ID}"
curl -fsS -N -H 'Content-Type: application/json' -H "X-User-Id: ${USER_ID}" \
  --data "$(payload "$SESSION_A" "$CANCEL_OP" "cancel")" "$A2A_URL" \
  >"$TMP_DIR/cancel.sse" 2>"$TMP_DIR/cancel.err" &
CANCEL_PID=$!
for _ in {1..180}; do
  grep -q '^data: ' "$TMP_DIR/cancel.sse" 2>/dev/null && break
  kill -0 "$CANCEL_PID" 2>/dev/null || fail "cancel stream exited before producing an update"
  sleep 1
done
grep -q '^data: ' "$TMP_DIR/cancel.sse" || fail "cancel stream never produced an update"
kill "$CANCEL_PID" >/dev/null 2>&1 || true
wait "$CANCEL_PID" 2>/dev/null || true

CANCEL_VERIFIED=false
for _ in {1..5}; do
  send_stream "$SESSION_A" "$CANCEL_OP" "duplicate-after-disconnect" "$TMP_DIR/cancel-replay.jsonl" || true
  if jq -se '([.[] | select(.result.final == true)] | length) == 1 and
    ([.[] | select(.result.final == true)] | last |
      .result.metadata.outcome == "canceled" and .result.metadata.duplicate == true)' \
    "$TMP_DIR/cancel-replay.jsonl" >/dev/null 2>&1; then
    CANCEL_VERIFIED=true
    break
  fi
  sleep 1
done
if [[ "$CANCEL_VERIFIED" == true ]]; then
  CANCEL_OUTCOME="verified: duplicate replay returned exactly one canceled terminal result with duplicate=true"
else
  CANCEL_OUTCOME="not verified: the client stream was terminated after its first update, but five duplicate replays did not expose a canceled terminal record; disconnect propagation through the local port-forward/A2A proxy remains a targeted follow-up"
fi

log "deleting context A and proving context B peer non-interference"
curl -fsS -X DELETE -H "X-User-Id: ${USER_ID}" "${KAGENT_URL}/api/sessions/${SESSION_A}" >/dev/null
wait_actor_absent "$ACTOR_A"
SESSION_A=""
send_stream "$SESSION_B" "b-after-delete-${RUN_ID}" "marker-read" "$TMP_DIR/b-after-delete.jsonl"
assert_completed_stream "$TMP_DIR/b-after-delete.jsonl"
grep -F "marker:actor-b" <<<"$(stream_text "$TMP_DIR/b-after-delete.jsonl")" >/dev/null || fail "peer context was interfered with by context A deletion"

log "cleaning up remaining session actor and fixture"
curl -fsS -X DELETE -H "X-User-Id: ${USER_ID}" "${KAGENT_URL}/api/sessions/${SESSION_B}" >/dev/null
wait_actor_absent "$ACTOR_B"
SESSION_B=""
kubectl --context "$KUBE_CONTEXT" -n "$NAMESPACE" delete -f "$TMP_DIR/fixture.yaml" --wait=true >/dev/null
APPLIED=false
for _ in {1..120}; do
  kubectl --context "$KUBE_CONTEXT" -n "$NAMESPACE" get actortemplate "$TEMPLATE_ID" >/dev/null 2>&1 || break
  sleep 1
done
kubectl --context "$KUBE_CONTEXT" -n "$NAMESPACE" get actortemplate "$TEMPLATE_ID" >/dev/null 2>&1 && fail "ActorTemplate ${TEMPLATE_ID} leaked after fixture cleanup"

mkdir -p "$(dirname "$EVIDENCE_FILE")"
cat > "$EVIDENCE_FILE" <<EOF
# Fake ACP Kind e2e evidence

- Date (UTC): $(date -u +%Y-%m-%dT%H:%M:%SZ)
- Base kagent commit: $(git -C "$ROOT_DIR" rev-parse HEAD)
- Prerequisite packaging commit: \`e2ae8df0\`
- Proof classification: **fake-runtime / local Kind**
- Image proof: **local-registry pull** (built with \`--load\`, pushed to the Kind-local registry, then pulled by the WorkerPool; not remote registry publication proof)
- Image tag: \`$IMAGE_TAG\`
- Local image ID: \`$IMAGE_ID\`
- WorkerPool image ref: \`$IMAGE_REF\`
- WorkerPool: \`$NAMESPACE/$WORKER_POOL\`
- ActorTemplate: \`$NAMESPACE/$TEMPLATE_ID\`
- Context A / actor / workspace / actor-scoped logical ACP session: \`$RUN_ID:A\` / \`$ACTOR_A\` / actor-local \`$WORKSPACE_A\` / \`$ACP_SESSION_A\`
- Context B / actor / workspace / actor-scoped logical ACP session: \`$RUN_ID:B\` / \`$ACTOR_B\` / actor-local \`$WORKSPACE_B\` / \`$ACP_SESSION_B\`
- Readiness: Ready ActorTemplate and SandboxAgent observed.
- Prompt streaming: multiple artifact updates and exactly one final completed status per successful request.
- Workspace isolation: marker \`actor-a\` remained visible only in context A; marker \`actor-b\` remained visible only in context B.
- Suspend/resume: context A reached a suspended/paused state, resumed, and retained its actor-local workspace marker.
- Immediate back-to-back turns: completed without a suspend race failure and retained context A's marker.
- Disconnect-as-cancel: $CANCEL_OUTCOME.
- Session deletion: context A actor disappeared; context B remained usable with its marker intact.
- Cleanup: both session actors, the SandboxAgent, and its ActorTemplate were removed. The local image and shared WorkerPool were intentionally retained.
- Redaction: generated test identifiers only; no credentials, sensitive prompts, environment dumps, or workspace contents were captured.
- Not proven here: remote registry publication/index digest, credentialed Codex ACP, or parent-Agent delegation.
EOF

log "fake ACP Kind e2e completed; evidence written to ${EVIDENCE_FILE}"
