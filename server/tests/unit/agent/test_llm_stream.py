"""Streamed OpenAI deltas accumulate tool arguments and text."""

import pytest

from superboss.core.llm import (
    LLMResult,
    LLMStreamChunk,
    apply_openai_delta,
    finished_tool_calls,
    iter_llm,
)


def test_content_and_tool_call_deltas_accumulate() -> None:
    tools: dict[int, dict[str, str]] = {}
    usage = {"prompt_tokens": 0, "completion_tokens": 0}
    first = apply_openai_delta(
        {"choices": [{"delta": {"content": "请"}}]},
        tools=tools,
        usage=usage,
    )
    second = apply_openai_delta(
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call-1",
                                "function": {"name": "propose_finance_entry", "arguments": "{"},
                            }
                        ]
                    }
                }
            ]
        },
        tools=tools,
        usage=usage,
    )
    third = apply_openai_delta(
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [{"index": 0, "function": {"arguments": '"kind":"COST"}'}}]
                    }
                }
            ],
            "usage": {"prompt_tokens": 11, "completion_tokens": 4},
        },
        tools=tools,
        usage=usage,
    )
    assert first == "请"
    assert second == ""
    assert third == ""
    calls = finished_tool_calls(tools)
    assert len(calls) == 1
    assert calls[0].name == "propose_finance_entry"
    assert '"kind":"COST"' in calls[0].arguments
    assert usage["prompt_tokens"] == 11


class _CompleteOnlyLLM:
    available = True

    async def complete(self, messages, tools) -> LLMResult:
        del messages, tools
        return LLMResult(content="请确认")


class _TokenLLM:
    available = True

    async def complete(self, messages, tools) -> LLMResult:
        del messages, tools
        return LLMResult(content="请确认")

    async def stream(self, messages, tools):
        del messages, tools
        for piece in "请确认":
            yield LLMStreamChunk(content=piece)
        yield LLMStreamChunk(done=True)


@pytest.mark.asyncio
async def test_iter_llm_falls_back_to_complete_without_stream() -> None:
    chunks = [chunk async for chunk in iter_llm(_CompleteOnlyLLM(), [], [])]
    assert [chunk.content for chunk in chunks if chunk.content] == ["请确认"]
    assert chunks[-1].done is True


@pytest.mark.asyncio
async def test_iter_llm_uses_stream_when_present() -> None:
    chunks = [chunk async for chunk in iter_llm(_TokenLLM(), [], [])]
    assert [chunk.content for chunk in chunks if chunk.content] == list("请确认")
    assert chunks[-1].done is True
