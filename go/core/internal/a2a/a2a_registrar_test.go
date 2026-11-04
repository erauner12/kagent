package a2a

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"

	a2atype "github.com/a2aproject/a2a-go/v2/a2a"
	"github.com/go-logr/logr"
	"github.com/kagent-dev/kagent/go/api/v1alpha2"
	"github.com/stretchr/testify/require"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

func TestRegistrarInvokesDeclaredWireProtocol(t *testing.T) {
	for _, tc := range []struct {
		name        string
		annotations map[string]string
		method      string
		result      string
		wantErr     bool
	}{
		{
			name:        "native Python BYO",
			annotations: map[string]string{v1alpha2.A2AProtocolVersionAnnotation: "1.0"},
			method:      "SendMessage",
			result:      `{"message":{"messageId":"reply","role":"ROLE_AGENT","parts":[{"text":"kebab"}]}}`,
		},
		{
			name:   "unchanged legacy BYO",
			method: "message/send",
			result: `{"kind":"message","messageId":"reply","role":"agent","parts":[{"kind":"text","text":"kebab"}]}`,
		},
		{
			name:    "undeclared native BYO refuses without retry",
			method:  "SendMessage",
			wantErr: true,
		},
	} {
		t.Run(tc.name, func(t *testing.T) {
			var calls atomic.Int32
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				calls.Add(1)
				var req struct {
					ID     json.RawMessage `json:"id"`
					Method string          `json:"method"`
				}
				if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
					t.Error(err)
					w.WriteHeader(http.StatusBadRequest)
					return
				}
				w.Header().Set("Content-Type", "application/json")
				if req.Method != tc.method {
					_, _ = fmt.Fprintf(w, `{"jsonrpc":"2.0","id":%s,"error":{"code":-32601,"message":"Method not found"}}`, req.ID)
					return
				}
				_, _ = fmt.Fprintf(w, `{"jsonrpc":"2.0","id":%s,"result":%s}`, req.ID, tc.result)
			}))
			t.Cleanup(server.Close)
			t.Setenv("KAGENT_A2A_DEBUG_ADDR", server.Listener.Addr().String())
			registry := NewAgentClientRegistry()
			registrar := &A2ARegistrar{
				handlerMux:     NewA2AHttpMux("/api/a2a", "/api/a2a/sandbox", nil, nil),
				clientRegistry: registry,
			}
			agent := &v1alpha2.Agent{
				ObjectMeta: metav1.ObjectMeta{Name: "kebab-agent", Namespace: "kagent", Annotations: tc.annotations},
				Spec:       v1alpha2.AgentSpec{Type: v1alpha2.AgentType_BYO},
			}
			require.NoError(t, registrar.upsertAgentHandler(context.Background(), agent, logr.Discard()))
			result, err := registry.SendMessage(context.Background(), "kagent", "kebab-agent", &a2atype.SendMessageRequest{
				Message: a2atype.NewMessage(a2atype.MessageRoleUser, a2atype.NewTextPart("What can you do?")),
			})
			require.Equal(t, int32(1), calls.Load(), "invocation must not retry on another protocol")
			if tc.wantErr {
				require.ErrorContains(t, err, "Method not found")
				return
			}
			require.NoError(t, err)
			reply, ok := result.(*a2atype.Message)
			require.True(t, ok)
			require.Equal(t, "kebab", ExtractText(reply))
		})
	}
}

func TestAgentProtocolVersion(t *testing.T) {
	for _, tc := range []struct {
		name       string
		spec       v1alpha2.AgentSpec
		annotation string
		want       a2atype.ProtocolVersion
		wantErr    bool
	}{
		{name: "Python", spec: v1alpha2.AgentSpec{Type: v1alpha2.AgentType_Declarative,
			Declarative: &v1alpha2.DeclarativeAgentSpec{Runtime: v1alpha2.DeclarativeRuntime_Python}}, want: a2atype.Version},
		{name: "implicit Python", spec: v1alpha2.AgentSpec{Type: v1alpha2.AgentType_Declarative,
			Declarative: &v1alpha2.DeclarativeAgentSpec{}}, want: a2atype.Version},
		{name: "unchanged Go", spec: v1alpha2.AgentSpec{Type: v1alpha2.AgentType_Declarative,
			Declarative: &v1alpha2.DeclarativeAgentSpec{Runtime: v1alpha2.DeclarativeRuntime_Go}}, want: "0.3"},
		{name: "unchanged BYO", spec: v1alpha2.AgentSpec{Type: v1alpha2.AgentType_BYO}, want: "0.3"},
		{name: "native BYO", spec: v1alpha2.AgentSpec{Type: v1alpha2.AgentType_BYO}, annotation: "1.0", want: a2atype.Version},
		{name: "legacy Python image override", spec: v1alpha2.AgentSpec{Type: v1alpha2.AgentType_Declarative,
			Declarative: &v1alpha2.DeclarativeAgentSpec{Runtime: v1alpha2.DeclarativeRuntime_Python}}, annotation: "0.3", want: "0.3"},
		{name: "unsupported version", annotation: "2.0", wantErr: true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			agent := &v1alpha2.Agent{ObjectMeta: metav1.ObjectMeta{
				Annotations: map[string]string{v1alpha2.A2AProtocolVersionAnnotation: tc.annotation},
			}, Spec: tc.spec}
			got, err := agentProtocolVersion(agent)
			if tc.wantErr {
				require.Error(t, err)
				return
			}
			require.NoError(t, err)
			require.Equal(t, tc.want, got)
		})
	}
}

func TestProtocolAnnotationRefreshesAgentConfiguration(t *testing.T) {
	agent := &v1alpha2.Agent{Spec: v1alpha2.AgentSpec{Type: v1alpha2.AgentType_BYO}}
	updated := agent.DeepCopy()
	updated.Annotations = map[string]string{"unrelated": "changed"}
	require.True(t, sameAgentConfiguration(agent, updated))
	updated.Annotations[v1alpha2.A2AProtocolVersionAnnotation] = "1.0"
	require.Equal(t, agent.Generation, updated.Generation, "annotation changes do not advance generation")
	require.False(t, sameAgentConfiguration(agent, updated))
	delete(updated.Annotations, v1alpha2.A2AProtocolVersionAnnotation)
	require.True(t, sameAgentConfiguration(agent, updated))
	updated.Spec.Description = "changed"
	require.False(t, sameAgentConfiguration(agent, updated))
}
