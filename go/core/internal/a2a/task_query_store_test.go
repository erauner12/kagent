package a2a

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	a2atype "github.com/a2aproject/a2a-go/v2/a2a"
	"github.com/a2aproject/a2a-go/v2/a2asrv"
	dbpkg "github.com/kagent-dev/kagent/go/api/database"
	"github.com/kagent-dev/kagent/go/core/pkg/auth"
	"github.com/stretchr/testify/require"
)

// fakeTaskStore is an in-memory TaskStore. Sessions are keyed by (id, userID)
// so cross-user isolation is exercised the same way the real store enforces it.
type fakeTaskStore struct {
	sessions     map[string]dbpkg.Session // key: sessionID -> session (carries UserID)
	tasks        map[string][]*a2atype.Task
	getTaskIDs   []string
	getTaskUsers []string
}

func newFakeStore() *fakeTaskStore {
	return &fakeTaskStore{
		sessions: map[string]dbpkg.Session{},
		tasks:    map[string][]*a2atype.Task{},
	}
}

func (f *fakeTaskStore) addSession(id, userID string) {
	f.sessions[id] = dbpkg.Session{ID: id, UserID: userID}
}

func (f *fakeTaskStore) addTask(sessionID string, task *a2atype.Task) {
	f.tasks[sessionID] = append(f.tasks[sessionID], task)
}

func (f *fakeTaskStore) GetTask(_ context.Context, taskID, userID string) (*a2atype.Task, error) {
	f.getTaskIDs = append(f.getTaskIDs, taskID)
	f.getTaskUsers = append(f.getTaskUsers, userID)
	for sessionID, tasks := range f.tasks {
		session, ok := f.sessions[sessionID]
		if !ok || session.UserID != userID {
			continue
		}
		for _, task := range tasks {
			if string(task.ID) == taskID {
				return task, nil
			}
		}
	}
	return nil, fmt.Errorf("task %s for user %s: %w", taskID, userID, dbpkg.ErrNotFound)
}

func (f *fakeTaskStore) GetSession(_ context.Context, sessionID, userID string) (*dbpkg.Session, error) {
	s, ok := f.sessions[sessionID]
	if !ok || s.UserID != userID {
		return nil, fmt.Errorf("session %s for user %s not found", sessionID, userID)
	}
	return &s, nil
}

func (f *fakeTaskStore) ListSessions(_ context.Context, userID string) ([]dbpkg.Session, error) {
	var out []dbpkg.Session
	for _, s := range f.sessions {
		if s.UserID == userID {
			out = append(out, s)
		}
	}
	return out, nil
}

func (f *fakeTaskStore) ListTasksForSession(_ context.Context, sessionID, userID string) ([]*a2atype.Task, error) {
	if s, ok := f.sessions[sessionID]; !ok || s.UserID != userID {
		return nil, nil
	}
	return f.tasks[sessionID], nil
}

// fakeSession injects a user principal into the request context.
type fakeSession struct{ user string }

func (f fakeSession) Principal() auth.Principal {
	return auth.Principal{User: auth.User{ID: f.user}}
}

func userCtx(user string) context.Context {
	return auth.AuthSessionTo(context.Background(), fakeSession{user: user})
}

func newTask(id, contextID string, state a2atype.TaskState, history, artifacts int) *a2atype.Task {
	ts := time.Date(2026, 7, 9, 12, 0, 0, 0, time.UTC)
	t := &a2atype.Task{
		ID:        a2atype.TaskID(id),
		ContextID: contextID,
		Status:    a2atype.TaskStatus{State: state, Timestamp: &ts},
	}
	for i := range history {
		t.History = append(t.History, &a2atype.Message{ID: fmt.Sprintf("%s-msg-%d", id, i), Role: a2atype.MessageRoleUser})
	}
	for i := range artifacts {
		t.Artifacts = append(t.Artifacts, &a2atype.Artifact{ID: a2atype.ArtifactID(fmt.Sprintf("%s-art-%d", id, i))})
	}
	return t
}

func storeWith(t *testing.T, user, session string, tasks ...*a2atype.Task) *storeTaskQueryHandler {
	t.Helper()
	store := newFakeStore()
	store.addSession(session, user)
	for _, tk := range tasks {
		store.addTask(session, tk)
	}
	return newStoreTaskQueryHandler(&PassthroughRequestHandler{}, store)
}

type recordingGetTaskDelegate struct {
	a2asrv.RequestHandler
	calls int
	req   *a2atype.GetTaskRequest
	task  *a2atype.Task
	err   error
}

func (d *recordingGetTaskDelegate) GetTask(_ context.Context, req *a2atype.GetTaskRequest) (*a2atype.Task, error) {
	d.calls++
	d.req = req
	return d.task, d.err
}

func TestGetTask_PersistentHitShapesHistoryAndIncludesArtifacts(t *testing.T) {
	zero := 0
	two := 2
	tests := []struct {
		name          string
		historyLength *int
		wantMessages  []string
	}{
		{name: "full history", wantMessages: []string{"t1-msg-0", "t1-msg-1", "t1-msg-2", "t1-msg-3"}},
		{name: "zero history", historyLength: &zero},
		{name: "bounded history", historyLength: &two, wantMessages: []string{"t1-msg-2", "t1-msg-3"}},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			stored := newTask("t1", "s1", a2atype.TaskStateCompleted, 4, 2)
			store := newFakeStore()
			store.addSession("s1", "alice")
			store.addTask("s1", stored)
			delegate := &recordingGetTaskDelegate{}
			h := newStoreTaskQueryHandler(delegate, store)

			got, err := h.GetTask(userCtx("alice"), &a2atype.GetTaskRequest{ID: "t1", HistoryLength: tt.historyLength})
			require.NoError(t, err)
			require.Equal(t, a2atype.TaskID("t1"), got.ID)
			require.Len(t, got.Artifacts, 2)
			require.Len(t, got.History, len(tt.wantMessages))
			for i, want := range tt.wantMessages {
				require.Equal(t, want, got.History[i].ID)
			}
			require.Equal(t, []string{"t1"}, store.getTaskIDs)
			require.Equal(t, []string{"alice"}, store.getTaskUsers)
			require.Zero(t, delegate.calls)
			require.Len(t, stored.History, 4)
			require.Len(t, stored.Artifacts, 2)
		})
	}
}

func TestGetTask_OtherOwnerDelegatesWithoutLeakingPersistedTask(t *testing.T) {
	store := newFakeStore()
	store.addSession("s1", "alice")
	store.addTask("s1", newTask("persisted", "s1", a2atype.TaskStateCompleted, 1, 1))
	delegateErr := fmt.Errorf("runtime task not found")
	delegate := &recordingGetTaskDelegate{err: delegateErr}
	h := newStoreTaskQueryHandler(delegate, store)
	req := &a2atype.GetTaskRequest{ID: "persisted"}

	got, err := h.GetTask(userCtx("mallory"), req)
	require.Nil(t, got)
	require.ErrorIs(t, err, delegateErr)
	require.Equal(t, []string{"mallory"}, store.getTaskUsers)
	require.Equal(t, 1, delegate.calls)
	require.Same(t, req, delegate.req)
}

func TestGetTask_PersistentMissDelegates(t *testing.T) {
	delegatedTask := newTask("runtime", "s1", a2atype.TaskStateWorking, 0, 0)
	req := &a2atype.GetTaskRequest{ID: "missing"}

	t.Run("result", func(t *testing.T) {
		delegate := &recordingGetTaskDelegate{task: delegatedTask}
		h := newStoreTaskQueryHandler(delegate, failingTaskStore{err: fmt.Errorf("lookup: %w", dbpkg.ErrNotFound)})

		got, err := h.GetTask(userCtx("alice"), req)
		require.NoError(t, err)
		require.Same(t, delegatedTask, got)
		require.Equal(t, 1, delegate.calls)
		require.Same(t, req, delegate.req)
	})

	t.Run("error", func(t *testing.T) {
		delegateErr := fmt.Errorf("runtime unavailable")
		delegate := &recordingGetTaskDelegate{err: delegateErr}
		h := newStoreTaskQueryHandler(delegate, failingTaskStore{err: fmt.Errorf("lookup: %w", dbpkg.ErrNotFound)})

		got, err := h.GetTask(userCtx("alice"), req)
		require.Nil(t, got)
		require.ErrorIs(t, err, delegateErr)
		require.Equal(t, 1, delegate.calls)
		require.Same(t, req, delegate.req)
	})
}

func TestGetTask_AbsentIdentityDelegatesWithoutStoreRead(t *testing.T) {
	store := newFakeStore()
	delegatedTask := newTask("runtime", "s1", a2atype.TaskStateWorking, 0, 0)
	delegate := &recordingGetTaskDelegate{task: delegatedTask}
	h := newStoreTaskQueryHandler(delegate, store)
	req := &a2atype.GetTaskRequest{ID: "runtime"}

	got, err := h.GetTask(context.Background(), req)
	require.NoError(t, err)
	require.Same(t, delegatedTask, got)
	require.Empty(t, store.getTaskIDs)
	require.Empty(t, store.getTaskUsers)
	require.Equal(t, 1, delegate.calls)
	require.Same(t, req, delegate.req)
}

func TestGetTask_BackendFailurePropagatesWithoutDelegation(t *testing.T) {
	backendErr := fmt.Errorf("database connection refused")
	delegate := &recordingGetTaskDelegate{}
	h := newStoreTaskQueryHandler(delegate, failingTaskStore{err: backendErr})

	got, err := h.GetTask(userCtx("alice"), &a2atype.GetTaskRequest{ID: "t1"})
	require.Nil(t, got)
	require.ErrorIs(t, err, backendErr)
	require.Zero(t, delegate.calls)
}

func TestListTasks_Pagination(t *testing.T) {
	tasks := []*a2atype.Task{
		newTask("t1", "s1", a2atype.TaskStateWorking, 0, 0),
		newTask("t2", "s1", a2atype.TaskStateWorking, 0, 0),
		newTask("t3", "s1", a2atype.TaskStateWorking, 0, 0),
		newTask("t4", "s1", a2atype.TaskStateWorking, 0, 0),
		newTask("t5", "s1", a2atype.TaskStateWorking, 0, 0),
	}
	h := storeWith(t, "alice", "s1", tasks...)
	ctx := userCtx("alice")

	var seen []string
	token := ""
	pages := 0
	for {
		resp, err := h.ListTasks(ctx, &a2atype.ListTasksRequest{ContextID: "s1", PageSize: 2, PageToken: token})
		require.NoError(t, err)
		require.Equal(t, 5, resp.TotalSize)
		require.Equal(t, 2, resp.PageSize)
		for _, tk := range resp.Tasks {
			seen = append(seen, string(tk.ID))
		}
		pages++
		if resp.NextPageToken == "" {
			break
		}
		token = resp.NextPageToken
		require.LessOrEqual(t, pages, 5, "pagination did not terminate")
	}
	require.Equal(t, 3, pages)
	require.Equal(t, []string{"t1", "t2", "t3", "t4", "t5"}, seen)
}

func TestListTasks_NextPageTokenAlwaysPresentEmptyOnLastPage(t *testing.T) {
	h := storeWith(t, "alice", "s1",
		newTask("t1", "s1", a2atype.TaskStateWorking, 0, 0),
		newTask("t2", "s1", a2atype.TaskStateWorking, 0, 0),
	)
	resp, err := h.ListTasks(userCtx("alice"), &a2atype.ListTasksRequest{ContextID: "s1", PageSize: 10})
	require.NoError(t, err)
	require.Len(t, resp.Tasks, 2)
	require.Equal(t, "", resp.NextPageToken, "nextPageToken must be empty string on the final page")

	// A context the caller can't see (missing or not theirs) surfaces as an error.
	_, err = h.ListTasks(userCtx("alice"), &a2atype.ListTasksRequest{ContextID: "does-not-exist"})
	require.Error(t, err)
}

func TestListTasks_IncludeArtifacts(t *testing.T) {
	h := storeWith(t, "alice", "s1", newTask("t1", "s1", a2atype.TaskStateCompleted, 0, 3))
	ctx := userCtx("alice")

	off, err := h.ListTasks(ctx, &a2atype.ListTasksRequest{ContextID: "s1"})
	require.NoError(t, err)
	require.Nil(t, off.Tasks[0].Artifacts, "artifacts must be omitted when includeArtifacts is false (default)")

	on, err := h.ListTasks(ctx, &a2atype.ListTasksRequest{ContextID: "s1", IncludeArtifacts: true})
	require.NoError(t, err)
	require.Len(t, on.Tasks[0].Artifacts, 3)
}

func TestListTasks_StatusFilter(t *testing.T) {
	h := storeWith(t, "alice", "s1",
		newTask("t1", "s1", a2atype.TaskStateWorking, 0, 0),
		newTask("t2", "s1", a2atype.TaskStateInputRequired, 0, 0),
		newTask("t3", "s1", a2atype.TaskStateInputRequired, 0, 0),
	)
	resp, err := h.ListTasks(userCtx("alice"), &a2atype.ListTasksRequest{ContextID: "s1", Status: a2atype.TaskStateInputRequired})
	require.NoError(t, err)
	require.Equal(t, 2, resp.TotalSize)
	for _, tk := range resp.Tasks {
		require.Equal(t, a2atype.TaskStateInputRequired, tk.Status.State)
	}
}

func TestListTasks_StatusTimestampAfter(t *testing.T) {
	early := time.Date(2026, 7, 1, 0, 0, 0, 0, time.UTC)
	late := time.Date(2026, 7, 8, 0, 0, 0, 0, time.UTC)
	old := newTask("t1", "s1", a2atype.TaskStateWorking, 0, 0)
	old.Status.Timestamp = &early
	recent := newTask("t2", "s1", a2atype.TaskStateWorking, 0, 0)
	recent.Status.Timestamp = &late

	h := storeWith(t, "alice", "s1", old, recent)
	cutoff := time.Date(2026, 7, 5, 0, 0, 0, 0, time.UTC)
	resp, err := h.ListTasks(userCtx("alice"), &a2atype.ListTasksRequest{ContextID: "s1", StatusTimestampAfter: &cutoff})
	require.NoError(t, err)
	require.Equal(t, 1, resp.TotalSize)
	require.Equal(t, "t2", string(resp.Tasks[0].ID))
}

func TestListTasks_HistoryLength(t *testing.T) {
	h := storeWith(t, "alice", "s1", newTask("t1", "s1", a2atype.TaskStateWorking, 5, 0))
	n := 2
	resp, err := h.ListTasks(userCtx("alice"), &a2atype.ListTasksRequest{ContextID: "s1", HistoryLength: &n})
	require.NoError(t, err)
	require.Len(t, resp.Tasks[0].History, 2)
	// The most recent messages are kept.
	require.Equal(t, "t1-msg-3", resp.Tasks[0].History[0].ID)
	require.Equal(t, "t1-msg-4", resp.Tasks[0].History[1].ID)
}

func TestCrossUserIsolation(t *testing.T) {
	store := newFakeStore()
	store.addSession("s1", "alice")
	store.addTask("s1", newTask("t1", "s1", a2atype.TaskStateWorking, 0, 0))
	h := newStoreTaskQueryHandler(&PassthroughRequestHandler{}, store)

	// mallory asks for alice's context id: denied, and the error does not
	// distinguish "not yours" from "does not exist".
	_, err := h.ListTasks(userCtx("mallory"), &a2atype.ListTasksRequest{ContextID: "s1"})
	require.Error(t, err)

	// Unauthenticated context: empty, never a leak (no user id, so the
	// session lookup is never attempted).
	respAnon, err := h.ListTasks(context.Background(), &a2atype.ListTasksRequest{ContextID: "s1"})
	require.NoError(t, err)
	require.Empty(t, respAnon.Tasks)
}

func TestListTasks_ShareContextGrantsOwnerSession(t *testing.T) {
	store := newFakeStore()
	store.addSession("s1", "alice")
	store.addTask("s1", newTask("t1", "s1", a2atype.TaskStateWorking, 0, 0))
	h := newStoreTaskQueryHandler(&PassthroughRequestHandler{}, store)

	// A visitor (bob) holding a share token for alice's session s1 lists it.
	ctx := auth.ShareContextTo(userCtx("bob"), &auth.ShareContext{SessionID: "s1", UserID: "alice"})
	resp, err := h.ListTasks(ctx, &a2atype.ListTasksRequest{ContextID: "s1"})
	require.NoError(t, err)
	require.Equal(t, 1, resp.TotalSize)
	require.Equal(t, "t1", string(resp.Tasks[0].ID))

	// A share token for a different session must not grant s1.
	ctxOther := auth.ShareContextTo(userCtx("bob"), &auth.ShareContext{SessionID: "s2", UserID: "alice"})
	_, err = h.ListTasks(ctxOther, &a2atype.ListTasksRequest{ContextID: "s1"})
	require.Error(t, err)

	// The share token must not widen an all-sessions query to the owner's tasks.
	respAll, err := h.ListTasks(ctx, &a2atype.ListTasksRequest{})
	require.NoError(t, err)
	require.Empty(t, respAll.Tasks, "share token grants one session, not the owner's whole account")
}

func TestListTasks_AcrossAllUserSessions(t *testing.T) {
	store := newFakeStore()
	store.addSession("s1", "alice")
	store.addSession("s2", "alice")
	store.addSession("s3", "bob")
	store.addTask("s1", newTask("t1", "s1", a2atype.TaskStateWorking, 0, 0))
	store.addTask("s2", newTask("t2", "s2", a2atype.TaskStateWorking, 0, 0))
	store.addTask("s3", newTask("t3", "s3", a2atype.TaskStateWorking, 0, 0))
	h := newStoreTaskQueryHandler(&PassthroughRequestHandler{}, store)

	resp, err := h.ListTasks(userCtx("alice"), &a2atype.ListTasksRequest{})
	require.NoError(t, err)
	require.Equal(t, 2, resp.TotalSize)
	got := []string{string(resp.Tasks[0].ID), string(resp.Tasks[1].ID)}
	require.ElementsMatch(t, []string{"t1", "t2"}, got)
}

// failingTaskStore fails every read with a backend error.
type failingTaskStore struct{ err error }

func (f failingTaskStore) GetTask(context.Context, string, string) (*a2atype.Task, error) {
	return nil, f.err
}

func (f failingTaskStore) GetSession(context.Context, string, string) (*dbpkg.Session, error) {
	return nil, f.err
}

func (f failingTaskStore) ListSessions(context.Context, string) ([]dbpkg.Session, error) {
	return nil, f.err
}

func (f failingTaskStore) ListTasksForSession(context.Context, string, string) ([]*a2atype.Task, error) {
	return nil, f.err
}

func TestListTasks_BackendFailurePropagates(t *testing.T) {
	backendErr := fmt.Errorf("failed to get session s1: connection refused")
	h := newStoreTaskQueryHandler(&PassthroughRequestHandler{}, failingTaskStore{err: backendErr})

	// A store failure must surface as an error, not an empty task list.
	_, err := h.ListTasks(userCtx("alice"), &a2atype.ListTasksRequest{ContextID: "s1"})
	require.ErrorContains(t, err, "connection refused")

	_, err = h.ListTasks(userCtx("alice"), &a2atype.ListTasksRequest{})
	require.ErrorContains(t, err, "connection refused")
}

// ── Wire tests ───────────────────────────────────────────────────────────────

func withUser(next http.Handler, user string) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		next.ServeHTTP(w, r.WithContext(auth.AuthSessionTo(r.Context(), fakeSession{user: user})))
	})
}

func rpcCall(t *testing.T, h http.Handler, body string) map[string]any {
	t.Helper()
	req := httptest.NewRequest(http.MethodPost, "/", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	require.Equal(t, http.StatusOK, rec.Code, "body: %s", rec.Body.String())
	var out map[string]any
	require.NoError(t, json.Unmarshal(rec.Body.Bytes(), &out), "raw: %s", rec.Body.String())
	return out
}

func wireHandler(user string, tasks ...*a2atype.Task) http.Handler {
	store := newFakeStore()
	store.addSession("s1", user)
	for _, tk := range tasks {
		store.addTask("s1", tk)
	}
	h := newStoreTaskQueryHandler(&PassthroughRequestHandler{}, store)
	return withUser(a2asrv.NewJSONRPCHandler(h), user)
}

func TestWire_ListTasksUsesV1StateCasing(t *testing.T) {
	tasks := []*a2atype.Task{
		newTask("t1", "s1", a2atype.TaskStateInputRequired, 0, 0),
		newTask("t2", "s1", a2atype.TaskStateWorking, 0, 0),
	}
	handler := wireHandler("alice", tasks...)

	resp := rpcCall(t, handler, `{"jsonrpc":"2.0","id":1,"method":"ListTasks","params":{"contextId":"s1","status":"TASK_STATE_INPUT_REQUIRED"}}`)
	result := resp["result"].(map[string]any)
	list := result["tasks"].([]any)
	require.Len(t, list, 1)
	require.Equal(t, float64(1), result["totalSize"])
	require.Contains(t, result, "nextPageToken")
	state := list[0].(map[string]any)["status"].(map[string]any)["state"].(string)
	require.Equal(t, "TASK_STATE_INPUT_REQUIRED", state)
}
