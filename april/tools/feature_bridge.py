"""Bridge: expose a Toolset through the legacy FeatureAdapter protocol.

The Phase 8 runtime uses Toolset directly; this adapter exists so the old
FeatureRuntime can adopt the new tool layer without a second implementation.
"""

from __future__ import annotations

from typing import Any

from .registry import Toolset


class ToolsetFeatureAdapter:
    name = "tools"

    def __init__(self, toolset: Toolset):
        self.toolset = toolset

    def pre_call(self, agent: Any, user_input: str) -> str:
        return self.toolset.context()

    def tool_schemas(self, agent: Any) -> list[dict]:
        return self.toolset.to_openai()

    def dispatch(self, tool_name: str, args: dict, agent: Any) -> str | None:
        if tool_name not in {schema.name for schema in self.toolset.schemas()}:
            return None
        return self.toolset.dispatch(tool_name, args).content

    def post_call(self, agent: Any, user_input: str, assistant_response: str) -> None:
        return None
