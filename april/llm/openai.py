"""OpenAI-compatible adapter for the Model seam."""

from __future__ import annotations

from typing import AsyncIterator

from openai import AsyncOpenAI, OpenAI

from .base import ChatReply, ChatRequest, Delta, ToolCall, ToolCallDelta


class OpenAIModel:
    """Talks to any OpenAI-compatible endpoint.

    shortcut: no retry or backoff here; callers own their error policy and the
    learning engine adds attempts when it ports (Phase 6).
    """

    def __init__(self, api_key: str, base_url: str, *, timeout: float | None = None):
        self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
        self.async_client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=timeout)

    def _payload(self, req: ChatRequest) -> dict:
        payload = {
            "model": req.model,
            "messages": req.messages,
            "temperature": req.temperature,
        }
        if req.tools:
            payload["tools"] = req.tools
        if req.response_format:
            payload["response_format"] = req.response_format
        return payload

    def complete(self, req: ChatRequest) -> ChatReply:
        response = self.client.chat.completions.create(**self._payload(req))
        message = response.choices[0].message
        return ChatReply(
            text=message.content or "",
            tool_calls=[
                ToolCall(id=tc.id, name=tc.function.name, arguments=tc.function.arguments)
                for tc in (message.tool_calls or [])
            ],
            usage=getattr(response, "usage", None),
            finish_reason=response.choices[0].finish_reason,
        )

    async def stream(self, req: ChatRequest) -> AsyncIterator[Delta]:
        payload = {**self._payload(req), "stream": True}
        stream = await self.async_client.chat.completions.create(**payload)
        async for chunk in stream:
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            delta = choice.delta
            calls = []
            for tc in getattr(delta, "tool_calls", None) or []:
                fn = getattr(tc, "function", None)
                calls.append(
                    ToolCallDelta(
                        index=tc.index,
                        id=tc.id or "",
                        name=getattr(fn, "name", "") or "",
                        arguments=getattr(fn, "arguments", "") or "",
                    )
                )
            yield Delta(
                text=getattr(delta, "content", None),
                tool_calls=calls,
                usage=getattr(chunk, "usage", None),
                finish_reason=choice.finish_reason,
            )
