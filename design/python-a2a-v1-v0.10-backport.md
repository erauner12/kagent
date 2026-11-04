# Draft: Python A2A 1.0 on the v0.10 release line

This is a fork-only feasibility backport of the Python portion of
[upstream #2354](https://github.com/kagent-dev/kagent/pull/2354), commit
`e4c1078ed8098edc318b3695d7807d5dfa363cca`, onto fork `release/v0.10.x`
at `c7a4013b838c973e71e34bd67d924f99c10cfa39`. It proposes a protocol
feature backport, not an ordinary patch-release fix or an upstream-supported
mixed-version deployment.

## Scope

The shared Python SDK changes from A2A SDK 0.3.26 to 1.1.2. Its message, part,
task, status and card types change from Pydantic models to protobuf messages.
The Python servers and remote-agent client use native A2A 1.0 routes and types;
there is no automatic protocol fallback. Existing 0.3 Python-runtime callers
must migrate alongside the runtime. The controller's existing compatibility
routes are unchanged; using them with this runtime remains an integration gate.

ADK, core, CrewAI, LangGraph and OpenAI share the SDK and workspace lock. Their
converters, request handlers, samples and affected tests therefore move together.
Preview package versions distinguish the changed API from existing releases;
consumers require the matching preview core. No packages or images are published.

Google ADK stays at the release's locked 1.38.0, with its existing flat
`AgentConfig`, skills/SRT implementation and session services. This change adds
no controller, Go-runtime, chart, CRD, UI, database or Substrate changes. Local
execution continues to use `KAgentApp.build(local=True)` and in-memory stores.
This backport does not add a new local CLI mode or reusable configuration export.

## Release-specific adjustments

- Preserve the release's task callback user scoping and serialized-session
  clarification/approval regressions when resolving the upstream patch.
- Parse the release controller's actual rendered cards by removing only its
  three legacy top-level fields and retaining its already-declared 1.0
  interfaces. Unknown fields still fail; a legacy-only card is not upgraded.
- Select `A2A-Version: 1.0` on task callbacks. This controller defaults to the
  legacy task representation when the header is absent.
- Assign the forwarded user in the SDK's HTTP context builder, before native
  1.0 task lookup. The existing forwarded-header trust boundary is unchanged.
- Close the ADK native handler through the existing application lifespan.

These are separate from the upstream protobuf conversion, and need particular
review. They are not a replacement protocol implementation.

## Validation and merge gates

The added deterministic tests exercise the real Python FastAPI application and
SDK 1.0 JSON-RPC dispatcher: request, `ask_user`, continuation using the returned
task/context, rejected task access under another user, and closed SSE responses.
They use the actual Google ADK runner with a fake model, not a paid endpoint.
The release's existing serialized-session approval/rejection/parallel-tool tests
remain. Controller card golden outputs are read without changing their bytes.

Before adopting an image, the outstanding gates are:

1. Build the exact runtime image and run it with the unchanged release controller
   and database: invocation/proxy routing, user ownership, task persistence,
   history, restart and clarification. HTTP mocks do not establish this pairing.
2. Qualify real MCP execution, executable skills/SRT, telemetry and retained
   evidence with that image, in managed and local arrangements as applicable.
3. Exercise cancellation, disconnected streams and shutdown. The executor's
   existing unsupported `CancelTask` behavior is not fixed by this backport.
4. Exercise native requests and shutdown for the other Python frameworks; their
   package unit tests are not an end-to-end runtime qualification.
5. Decide whether upstream accepts this breaking feature on a maintenance line
   before proposing it there. Agreement on the 1.x implementation is not
   agreement on a 0.10 backport.

The conservative alternative is to retain the native 0.3 Python integration
until a coordinated runtime/controller upgrade. A standalone newer runtime is
a separate evaluated configuration, not evidence of old-controller compatibility.
