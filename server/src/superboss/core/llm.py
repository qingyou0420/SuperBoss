"""OpenAI-compatible chat-completions client with tools and streaming."""

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from superboss.core.config import Settings


class LLMUnavailable(Exception):
    """The configured model endpoint cannot be used."""


@dataclass
class LLMToolCall:
    id: str
    name: str
    arguments: str


@dataclass
class LLMResult:
    content: str = ""
    tool_calls: list[LLMToolCall] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass
class LLMStreamChunk:
    content: str = ""
    tool_calls: list[LLMToolCall] | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    done: bool = False


class LLMClient(Protocol):
    available: bool

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> LLMResult: ...

    def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> AsyncIterator[LLMStreamChunk]: ...


def apply_openai_delta(
    body: dict[str, Any],
    *,
    tools: dict[int, dict[str, str]],
    usage: dict[str, int],
) -> str:
    """Apply one streamed chat-completions object; return any content delta."""
    usage_body = body.get("usage") or {}
    if isinstance(usage_body, dict) and usage_body:
        usage["prompt_tokens"] = int(usage_body.get("prompt_tokens") or 0)
        usage["completion_tokens"] = int(usage_body.get("completion_tokens") or 0)
    choice = (body.get("choices") or [{}])[0]
    if not isinstance(choice, dict):
        return ""
    delta = choice.get("delta") or {}
    if not isinstance(delta, dict):
        return ""
    for item in delta.get("tool_calls") or []:
        if not isinstance(item, dict):
            continue
        index = int(item.get("index") or 0)
        current = tools.setdefault(index, {"id": "", "name": "", "arguments": ""})
        if item.get("id"):
            current["id"] = str(item["id"])
        function = item.get("function") or {}
        if isinstance(function, dict):
            if function.get("name"):
                current["name"] = str(function["name"])
            if function.get("arguments"):
                current["arguments"] += str(function["arguments"])
    piece = delta.get("content")
    return str(piece) if piece else ""


def finished_tool_calls(collected: dict[int, dict[str, str]]) -> list[LLMToolCall]:
    return [
        LLMToolCall(id=item["id"], name=item["name"], arguments=item["arguments"] or "{}")
        for _, item in sorted(collected.items())
        if item["name"]
    ]


class OfflineLLM:
    available = False

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> LLMResult:
        del messages, tools
        raise LLMUnavailable()

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> AsyncIterator[LLMStreamChunk]:
        del messages, tools
        raise LLMUnavailable()
        yield LLMStreamChunk(done=True)  # pragma: no cover


class OpenAICompatibleLLM:
    def __init__(self, settings: Settings) -> None:
        self.base_url = settings.llm_base_url.rstrip("/")
        self.api_key = settings.llm_api_key
        self.model = settings.llm_model
        self.timeout = settings.llm_timeout_seconds
        self.available = bool(self.base_url and self.api_key and self.model)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _payload(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], *, stream: bool
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"model": self.model, "messages": messages, "stream": stream}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if stream:
            payload["stream_options"] = {"include_usage": True}
        return payload

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> LLMResult:
        if not self.available:
            raise LLMUnavailable()
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    json=self._payload(messages, tools, stream=False),
                    headers=self._headers(),
                )
                response.raise_for_status()
                body = response.json()
        except Exception as error:
            raise LLMUnavailable() from error
        choice = (body.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        usage = body.get("usage") or {}
        tool_calls = []
        for item in message.get("tool_calls") or []:
            function = item.get("function") or {}
            tool_calls.append(
                LLMToolCall(
                    id=str(item.get("id") or ""),
                    name=str(function.get("name") or ""),
                    arguments=str(function.get("arguments") or "{}"),
                )
            )
        return LLMResult(
            content=str(message.get("content") or ""),
            tool_calls=tool_calls,
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
        )

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> AsyncIterator[LLMStreamChunk]:
        if not self.available:
            raise LLMUnavailable()
        collected: dict[int, dict[str, str]] = {}
        usage = {"prompt_tokens": 0, "completion_tokens": 0}
        try:
            async with (
                httpx.AsyncClient(timeout=self.timeout) as client,
                client.stream(
                    "POST",
                    f"{self.base_url}/chat/completions",
                    json=self._payload(messages, tools, stream=True),
                    headers=self._headers(),
                ) as response,
            ):
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        body = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(body, dict):
                        continue
                    piece = apply_openai_delta(body, tools=collected, usage=usage)
                    if piece:
                        yield LLMStreamChunk(content=piece)
        except LLMUnavailable:
            raise
        except Exception as error:
            raise LLMUnavailable() from error
        yield LLMStreamChunk(
            done=True,
            tool_calls=finished_tool_calls(collected),
            prompt_tokens=usage["prompt_tokens"],
            completion_tokens=usage["completion_tokens"],
        )


async def iter_llm(
    client: LLMClient,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
) -> AsyncIterator[LLMStreamChunk]:
    stream = getattr(client, "stream", None)
    if callable(stream):
        async for chunk in stream(messages, tools):
            yield chunk
        return
    result = await client.complete(messages, tools)
    if result.content:
        yield LLMStreamChunk(content=result.content)
    yield LLMStreamChunk(
        done=True,
        tool_calls=result.tool_calls,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
    )


def llm_from_settings(settings: Settings) -> LLMClient:
    client = OpenAICompatibleLLM(settings)
    return client if client.available else OfflineLLM()
