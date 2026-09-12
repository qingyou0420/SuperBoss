"""OpenAI-compatible history replay for stored tool rounds."""

from types import SimpleNamespace

from superboss.modules.agent.models import MessageRole
from superboss.modules.agent.service import recall_needles, replay_history, replay_window_row


def test_replay_puts_tool_call_id_after_assistant_tool_calls() -> None:
    assistant = replay_window_row(
        role="assistant",
        content="",
        tool_calls={
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "list_projects", "arguments": "{}"},
                }
            ]
        },
    )
    tool = replay_window_row(
        role="tool",
        content="[]",
        tool_calls={"tool_call_id": "call-1"},
    )
    assert assistant["role"] == "assistant"
    assert assistant["tool_calls"][0]["id"] == "call-1"
    assert tool == {"role": "tool", "content": "[]", "tool_call_id": "call-1"}


def test_replay_history_pairs_tools_and_moves_user_first() -> None:
    rows = [
        SimpleNamespace(
            seq=1,
            role=MessageRole.ASSISTANT,
            content="查一下",
            tool_calls={
                "tool_calls": [
                    {
                        "id": "recall_memory_0",
                        "type": "function",
                        "function": {"name": "recall_memory", "arguments": "{}"},
                    },
                    {
                        "id": "list_projects_1",
                        "type": "function",
                        "function": {"name": "list_projects", "arguments": "{}"},
                    },
                ]
            },
        ),
        SimpleNamespace(
            seq=2,
            role=MessageRole.TOOL,
            content="[]",
            tool_calls={"tool_call_id": "list_projects_1"},
        ),
        SimpleNamespace(
            seq=3,
            role=MessageRole.USER,
            content="昨天那个合作项目",
            tool_calls={},
        ),
        SimpleNamespace(
            seq=4,
            role=MessageRole.TOOL,
            content="[]",
            tool_calls={"tool_call_id": "recall_memory_0"},
        ),
        SimpleNamespace(
            seq=5,
            role=MessageRole.ASSISTANT,
            content="星野合作",
            tool_calls={},
        ),
    ]
    window = replay_history(rows)
    assert [item["role"] for item in window] == [
        "user",
        "assistant",
        "tool",
        "tool",
        "assistant",
    ]
    assert window[0]["content"] == "昨天那个合作项目"
    assert window[1]["tool_calls"][0]["id"] == "recall_memory_0"
    assert window[2]["tool_call_id"] == "recall_memory_0"
    assert window[3]["tool_call_id"] == "list_projects_1"
    assert window[4]["content"] == "星野合作"


def test_replay_history_stubs_missing_tool_row() -> None:
    rows = [
        SimpleNamespace(
            seq=1,
            role=MessageRole.USER,
            content="列项目",
            tool_calls={},
        ),
        SimpleNamespace(
            seq=2,
            role=MessageRole.ASSISTANT,
            content="",
            tool_calls={
                "tool_calls": [
                    {
                        "id": "list_projects_1",
                        "type": "function",
                        "function": {"name": "list_projects", "arguments": "{}"},
                    }
                ]
            },
        ),
    ]
    window = replay_history(rows)
    assert window[1]["tool_calls"][0]["id"] == "list_projects_1"
    assert window[2] == {"role": "tool", "tool_call_id": "list_projects_1", "content": "{}"}


def test_recall_needles_split_chinese_query() -> None:
    needles = recall_needles("昨天那个合作项目")
    assert "合作" in needles or any("合作" in item for item in needles)
