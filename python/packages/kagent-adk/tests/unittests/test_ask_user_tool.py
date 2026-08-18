import json

import pytest
from google.adk.tools.tool_confirmation import ToolConfirmation

from kagent.adk.tools.ask_user_tool import AskUserTool


class RecordingToolContext:
    def __init__(self, tool_confirmation=None):
        self._tool_confirmation = tool_confirmation
        self.confirmation_inspections = 0
        self.confirmation_requests = []

    @property
    def tool_confirmation(self):
        self.confirmation_inspections += 1
        return self._tool_confirmation

    def request_confirmation(self, *, hint=None, payload=None):
        self.confirmation_requests.append({"hint": hint, "payload": payload})


@pytest.mark.asyncio
async def test_rejects_empty_questions_before_confirmation():
    context = RecordingToolContext()

    with pytest.raises(ValueError, match=r"^ask_user: at least one question is required$"):
        await AskUserTool().run_async(args={"questions": []}, tool_context=context)

    assert context.confirmation_inspections == 0
    assert context.confirmation_requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("question", ["", "   ", "\t\n"])
async def test_rejects_blank_question_before_confirmation(question):
    context = RecordingToolContext()

    with pytest.raises(ValueError, match=r"^ask_user: question 1 must contain non-whitespace text$"):
        await AskUserTool().run_async(
            args={"questions": [{"question": question}]},
            tool_context=context,
        )

    assert context.confirmation_inspections == 0
    assert context.confirmation_requests == []


@pytest.mark.asyncio
async def test_rejects_blank_second_question_before_confirmation():
    context = RecordingToolContext()

    with pytest.raises(ValueError, match=r"^ask_user: question 2 must contain non-whitespace text$"):
        await AskUserTool().run_async(
            args={
                "questions": [
                    {"question": "Which environment?"},
                    {"question": " \t\n"},
                ]
            },
            tool_context=context,
        )

    assert context.confirmation_inspections == 0
    assert context.confirmation_requests == []


@pytest.mark.asyncio
async def test_valid_question_requests_confirmation_without_rewriting_text():
    context = RecordingToolContext()
    questions = [
        {
            "question": "  Which environment?  ",
            "choices": ["prod", "staging"],
            "multiple": True,
        }
    ]

    result = await AskUserTool().run_async(
        args={"questions": questions},
        tool_context=context,
    )

    assert result == {"status": "pending", "questions": questions}
    assert context.confirmation_inspections == 1
    assert context.confirmation_requests == [
        {
            "hint": "  Which environment?  ",
            "payload": None,
        }
    ]


@pytest.mark.asyncio
async def test_valid_confirmed_question_returns_answer_without_new_confirmation():
    context = RecordingToolContext(
        ToolConfirmation(
            confirmed=True,
            payload={"answers": [{"answer": "prod"}]},
        )
    )

    result = await AskUserTool().run_async(
        args={"questions": [{"question": "  Which environment?  "}]},
        tool_context=context,
    )

    assert json.loads(result) == [
        {
            "question": "  Which environment?  ",
            "answer": "prod",
        }
    ]
    assert context.confirmation_inspections > 0
    assert context.confirmation_requests == []
