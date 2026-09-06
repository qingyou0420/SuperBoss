"""OpenAI-compatible history replay for stored tool rounds."""

from superboss.modules.agent.service import recall_needles, replay_window_row


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


def test_recall_needles_split_chinese_query() -> None:
    needles = recall_needles("昨天那个合作项目")
    assert "合作" in needles or any("合作" in item for item in needles)
