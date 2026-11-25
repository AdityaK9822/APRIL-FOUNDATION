"""The LLM seam: one request/response interface for every model call.

Deliberately dumb: no tool loop, no sessions, no persona. The runtime and the
learning engine build their own loops on top (decision B, rebuild-plan.md).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Protocol, runtime_checkable


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass
class ToolCallDelta:
    index: int
    id: str = ""
    name: str = ""
    arguments: str = ""


@dataclass
class ChatRequest:
    model: str
    messages: list[dict]
    temperature: float = 0.7
    tools: list[dict] | None = None
    response_format: dict | None = None


@dataclass
class ChatReply:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Any = None
    finish_reason: str | None = None


@dataclass
class Delta:
    text: str | None = None
    tool_calls: list[ToolCallDelta] = field(default_factory=list)
    usage: Any = None
    finish_reason: str | None = None


@runtime_checkable
class Model(Protocol):
    """The one LLM interface. Two adapters: openai-compatible, fake."""

    def complete(self, req: ChatRequest) -> ChatReply: ...

    def stream(self, req: ChatRequest) -> AsyncIterator[Delta]: ...
