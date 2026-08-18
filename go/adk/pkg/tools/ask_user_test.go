package tools

import (
	"encoding/json"
	"reflect"
	"testing"

	adkagent "google.golang.org/adk/v2/agent"
	"google.golang.org/adk/v2/tool/toolconfirmation"
)

type askUserConfirmationRequest struct {
	hint    string
	payload any
}

type askUserTestContext struct {
	adkagent.Context
	confirmation        *toolconfirmation.ToolConfirmation
	confirmationLookups int
	requests            []askUserConfirmationRequest
}

func (c *askUserTestContext) ToolConfirmation() *toolconfirmation.ToolConfirmation {
	c.confirmationLookups++
	return c.confirmation
}

func (c *askUserTestContext) askUserConfirmationLookups() int {
	// functiontool.Run performs one framework preflight lookup before it
	// dispatches the handler. Any additional lookup belongs to ask_user.
	return c.confirmationLookups - 1
}

func (c *askUserTestContext) RequestConfirmation(hint string, payload any) error {
	c.requests = append(c.requests, askUserConfirmationRequest{hint: hint, payload: payload})
	return nil
}

func runAskUserTool(
	t *testing.T,
	ctx adkagent.Context,
	args map[string]any,
) (map[string]any, error) {
	t.Helper()

	askUserTool, err := NewAskUserTool()
	if err != nil {
		t.Fatalf("NewAskUserTool() error = %v", err)
	}
	runner, ok := askUserTool.(interface {
		Run(adkagent.Context, any) (map[string]any, error)
	})
	if !ok {
		t.Fatalf("ask_user tool %T does not implement Run", askUserTool)
	}
	return runner.Run(ctx, args)
}

func assertJSONEqual(t *testing.T, got any, want string) {
	t.Helper()

	gotJSON, err := json.Marshal(got)
	if err != nil {
		t.Fatalf("json.Marshal(%v) error = %v", got, err)
	}
	var gotValue any
	if err := json.Unmarshal(gotJSON, &gotValue); err != nil {
		t.Fatalf("json.Unmarshal(got) error = %v", err)
	}
	var wantValue any
	if err := json.Unmarshal([]byte(want), &wantValue); err != nil {
		t.Fatalf("json.Unmarshal(want) error = %v", err)
	}
	if !reflect.DeepEqual(gotValue, wantValue) {
		t.Fatalf("result = %s, want %s", gotJSON, want)
	}
}

func TestAskUserRejectsEmptyQuestionsWithoutRequestingConfirmation(t *testing.T) {
	ctx := &askUserTestContext{}

	_, err := runAskUserTool(t, ctx, map[string]any{"questions": []any{}})

	if err == nil || err.Error() != "ask_user: at least one question is required" {
		t.Fatalf("error = %v, want empty-questions validation error", err)
	}
	if got := ctx.askUserConfirmationLookups(); got != 0 {
		t.Fatalf("ask_user confirmation lookups = %d, want 0", got)
	}
	if len(ctx.requests) != 0 {
		t.Fatalf("confirmation requests = %d, want 0", len(ctx.requests))
	}
}

func TestAskUserRejectsBlankQuestionWithoutRequestingConfirmation(t *testing.T) {
	for _, question := range []string{"", "   ", "\t\n"} {
		t.Run(question, func(t *testing.T) {
			ctx := &askUserTestContext{}

			_, err := runAskUserTool(t, ctx, map[string]any{
				"questions": []any{map[string]any{"question": question}},
			})

			if err == nil || err.Error() != "ask_user: question 1 must contain non-whitespace text" {
				t.Fatalf("error = %v, want blank-question validation error", err)
			}
			if got := ctx.askUserConfirmationLookups(); got != 0 {
				t.Fatalf("ask_user confirmation lookups = %d, want 0", got)
			}
			if len(ctx.requests) != 0 {
				t.Fatalf("confirmation requests = %d, want 0", len(ctx.requests))
			}
		})
	}
}

func TestAskUserRejectsBlankSecondQuestionBeforeConfirmation(t *testing.T) {
	ctx := &askUserTestContext{}

	_, err := runAskUserTool(t, ctx, map[string]any{
		"questions": []any{
			map[string]any{"question": "Which environment?"},
			map[string]any{"question": " \t\n"},
		},
	})

	if err == nil || err.Error() != "ask_user: question 2 must contain non-whitespace text" {
		t.Fatalf("error = %v, want indexed blank-question validation error", err)
	}
	if got := ctx.askUserConfirmationLookups(); got != 0 {
		t.Fatalf("ask_user confirmation lookups = %d, want 0", got)
	}
	if len(ctx.requests) != 0 {
		t.Fatalf("confirmation requests = %d, want 0", len(ctx.requests))
	}
}

func TestAskUserValidQuestionRequestsConfirmation(t *testing.T) {
	ctx := &askUserTestContext{}
	question := "  Which environment?  "

	result, err := runAskUserTool(t, ctx, map[string]any{
		"questions": []any{map[string]any{
			"question": question,
			"choices":  []any{"prod", "staging"},
			"multiple": true,
		}},
	})

	if err != nil {
		t.Fatalf("Run() error = %v", err)
	}
	if len(ctx.requests) != 1 {
		t.Fatalf("confirmation requests = %d, want 1", len(ctx.requests))
	}
	if ctx.requests[0].hint != question {
		t.Fatalf("confirmation hint = %q, want %q", ctx.requests[0].hint, question)
	}
	if ctx.requests[0].payload != nil {
		t.Fatalf("confirmation payload = %v, want nil", ctx.requests[0].payload)
	}
	assertJSONEqual(t, result, `{"status":"pending","questions":[{"question":"  Which environment?  ","choices":["prod","staging"],"multiple":true}]}`)
}

func TestAskUserValidConfirmedQuestionReturnsAnswer(t *testing.T) {
	ctx := &askUserTestContext{
		confirmation: &toolconfirmation.ToolConfirmation{
			Confirmed: true,
			Payload: map[string]any{
				"answers": []any{map[string]any{"answer": "prod"}},
			},
		},
	}

	result, err := runAskUserTool(t, ctx, map[string]any{
		"questions": []any{map[string]any{"question": "  Which environment?  "}},
	})

	if err != nil {
		t.Fatalf("Run() error = %v", err)
	}
	if len(ctx.requests) != 0 {
		t.Fatalf("confirmation requests = %d, want 0", len(ctx.requests))
	}
	assertJSONEqual(t, result, `{"result":"[{\"answer\":\"prod\",\"question\":\"  Which environment?  \"}]"}`)
}
