from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from a2a.server.context import ServerCallContext
from a2a.types import ListTasksRequest, Task, TaskState, TaskStatus
from google.protobuf.json_format import MessageToDict
from google.protobuf.timestamp_pb2 import Timestamp

from kagent.core.a2a import KAgentTaskStore
from kagent.core.a2a._context import get_request_user_id, set_request_user_id


@pytest.mark.asyncio
async def test_get_scopes_user_from_server_call_context():
    observed_users: list[str | None] = []
    response = MagicMock(status_code=404)

    async def get_task(_: str, **kwargs):
        observed_users.append(get_request_user_id())
        return response

    client = MagicMock()
    client.get = AsyncMock(side_effect=get_task)
    store = KAgentTaskStore(client)
    context = ServerCallContext(state={"headers": {"x-user-id": "user-1"}})
    set_request_user_id("ambient-user")
    try:
        result = await store.get("task-1", context=context)

        assert result is None
        assert observed_users == ["user-1"]
        assert get_request_user_id() == "ambient-user"
    finally:
        set_request_user_id(None)


@pytest.mark.asyncio
async def test_get_restores_user_when_request_fails():
    client = MagicMock()
    client.get = AsyncMock(side_effect=RuntimeError("controller unavailable"))
    store = KAgentTaskStore(client)
    context = ServerCallContext(state={"headers": {"x-user-id": "user-1"}})
    set_request_user_id("ambient-user")
    try:
        with pytest.raises(RuntimeError, match="controller unavailable"):
            await store.get("task-1", context=context)

        assert get_request_user_id() == "ambient-user"
    finally:
        set_request_user_id(None)


@pytest.mark.asyncio
async def test_get_without_scoped_user_clears_then_restores_ambient_user():
    observed_users: list[str | None] = []

    async def get_task(_: str, **kwargs):
        observed_users.append(get_request_user_id())
        return MagicMock(status_code=404)

    client = MagicMock()
    client.get = AsyncMock(side_effect=get_task)
    store = KAgentTaskStore(client)
    context = ServerCallContext(state={"headers": {}})
    set_request_user_id("ambient-user")
    try:
        result = await store.get("task-1", context=context)

        assert result is None
        assert observed_users == [None]
        assert get_request_user_id() == "ambient-user"
    finally:
        set_request_user_id(None)


@pytest.mark.asyncio
async def test_list_requires_context_id():
    client = httpx.AsyncClient(base_url="http://kagent.local")
    store = KAgentTaskStore(client)

    resp = await store.list(ListTasksRequest())
    assert len(resp.tasks) == 0
    assert resp.total_size == 0

    await client.aclose()


@pytest.mark.asyncio
async def test_list_filters_status_and_supports_paging():
    ts = Timestamp()
    ts.GetCurrentTime()
    task_working = Task(
        id="t-working",
        context_id="ctx-1",
        status=TaskStatus(state=TaskState.TASK_STATE_WORKING, timestamp=ts),
    )
    task_done = Task(
        id="t-done",
        context_id="ctx-1",
        status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED, timestamp=ts),
    )
    payload = {
        "data": [MessageToDict(task_working), MessageToDict(task_done)],
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/sessions/ctx-1/tasks"
        return httpx.Response(200, json=payload)

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(base_url="http://kagent.local", transport=transport)
    store = KAgentTaskStore(client)

    resp = await store.list(
        ListTasksRequest(
            context_id="ctx-1",
            status=TaskState.TASK_STATE_WORKING,
            page_size=1,
        )
    )

    assert resp.total_size == 1
    assert resp.page_size == 1
    assert len(resp.tasks) == 1
    assert resp.tasks[0].id == "t-working"

    await client.aclose()


@pytest.mark.asyncio
async def test_release_controller_callbacks_select_v1_and_preserve_user_scope():
    task = Task(id="task-1", context_id="session-1", status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED))
    observed = []

    def controller(request: httpx.Request) -> httpx.Response:
        # The release controller defaults to 0.3 unless the callback opts into 1.0.
        assert request.headers.get("A2A-Version") == "1.0"
        assert get_request_user_id() == "user-1"
        observed.append((request.method, request.url.path))
        data = MessageToDict(task)
        if request.url.path == "/api/sessions/session-1/tasks":
            data = [data]
        return httpx.Response(200, json={"data": data})

    context = ServerCallContext(state={"headers": {"x-user-id": "user-1"}})
    set_request_user_id("ambient-user")
    try:
        async with httpx.AsyncClient(base_url="http://controller", transport=httpx.MockTransport(controller)) as client:
            store = KAgentTaskStore(client)
            await store.save(task, context=context)
            assert await store.get(task.id, context=context) == task
            assert list((await store.list(ListTasksRequest(context_id=task.context_id), context=context)).tasks) == [
                task
            ]
            await store.delete(task.id, context=context)
        assert observed == [
            ("POST", "/api/tasks"),
            ("GET", "/api/tasks/task-1"),
            ("GET", "/api/sessions/session-1/tasks"),
            ("DELETE", "/api/tasks/task-1"),
        ]
        assert get_request_user_id() == "ambient-user"
    finally:
        set_request_user_id(None)
