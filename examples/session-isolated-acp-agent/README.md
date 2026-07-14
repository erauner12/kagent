# Session-isolated ACP agent fixtures

These manifests prepare the image-packaging shape for later Kind validation;
they do not constitute deployment or delegation proof.

1. Replace the `sha256:aaaa...` sentinel in `sandbox-agent.yaml` with the
   published `a2a-codex-sandbox` multi-architecture index digest.
2. Create the runtime-only credential Secret:

   ```sh
   kubectl create secret generic a2a-codex-sandbox-credentials \
     --namespace kagent \
     --from-literal=OPENAI_API_KEY="$OPENAI_API_KEY"
   ```

3. Apply `sandbox-agent.yaml`. Apply `coordinator-agent.yaml` only when running
   the later parent-delegation acceptance lane.

The SandboxAgent uses an explicit command as required by the substrate BYO
ActorTemplate path. Credentials are referenced only through runtime container
environment variables; they are not image build arguments or image content.
