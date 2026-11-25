"""The tool seam: one schema, one result, one source; hooks are typed events."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Protocol, runtime_checkable


class HookEvent(str, Enum):
    """The seven ZCode v2 hook events."""

    SESSION_START = "SessionStart"
    USER_PROMPT_SUBMIT = "UserPromptSubmit"
    PRE_TOOL_USE = "PreToolUse"
    PERMISSION_REQUEST = "PermissionRequest"
    POST_TOOL_USE = "PostToolUse"
    POST_TOOL_USE_FAILURE = "PostToolUseFailure"
    STOP = "Stop"


HOOK_EVENTS = tuple(event.value for event in HookEvent)


@dataclass(frozen=True)
class ToolSchema:
    name: str
    description: str
    parameters: dict = field(default_factory=dict)

    def to_openai(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    content: str

    def __str__(self) -> str:
        return self.content


@dataclass(frozen=True)
class HookDecision:
    allow: bool = True
    reason: str = ""
    context: str = ""


@runtime_checkable
class ToolSource(Protocol):
    """One contributor of tools. The Toolset aggregates them."""

    name: str

    def schemas(self) -> list[ToolSchema]: ...

    def dispatch(self, name: str, args: dict) -> Optional[ToolResult]: ...

    def context(self) -> str: ...
