"""Deterministic Model adapter for tests and the offline self-check."""

from __future__ import annotations

from typing import AsyncIterator

from .base import ChatReply, ChatRequest, Delta, ToolCallDelta


class FakeModel:
    """Replays scripted replies in order; the last reply repeats."""

    def __init__(self, replies: list[ChatReply] | None = None):
        self._replies = list(replies or [ChatReply(text="ok")])
        self._index = 0
        self.requests: list[ChatRequest] = []

    def _next(self, req: ChatRequest) -> ChatReply:
        self.requests.append(req)
        reply = self._replies[min(self._index, len(self._replies) - 1)]
        self._index += 1
        return reply

    def complete(self, req: ChatRequest) -> ChatReply:
        return self._next(req)

    async def stream(self, req: ChatRequest) -> AsyncIterator[Delta]:
        reply = self._next(req)
        words = reply.text.split(" ")
        for index, word in enumerate(words):
            suffix = " " if index < len(words) - 1 else ""
            yield Delta(text=word + suffix)
        for call in reply.tool_calls:
            yield Delta(
                tool_calls=[
                    ToolCallDelta(
                        index=0, id=call.id, name=call.name, arguments=call.arguments
                    )
                ]
            )
        yield Delta(finish_reason=reply.finish_reason or "stop")
