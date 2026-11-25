"""The Toolset: the one tool seam the runtime sees."""

from __future__ import annotations

from typing import Any

from .base import HookDecision, HookEvent, ToolResult, ToolSchema, ToolSource


class Toolset:
    def __init__(self, sources: list[ToolSource], hooks: Any = None):
        self.sources = list(sources)
        self.hooks = hooks

    def schemas(self) -> list[ToolSchema]:
        seen: dict[str, ToolSchema] = {}
        for source in self.sources:
            for schema in source.schemas():
                seen.setdefault(schema.name, schema)
        return list(seen.values())

    def to_openai(self) -> list[dict]:
        return [schema.to_openai() for schema in self.schemas()]

    def context(self) -> str:
        parts = [source.context() for source in self.sources]
        return "\n\n".join(part for part in parts if part)

    def dispatch(self, name: str, args: dict | None = None) -> ToolResult:
        args = dict(args or {})
        if self.hooks is not None:
            decision = self.hooks.fire(
                HookEvent.PRE_TOOL_USE, {"tool_name": name, "tool_input": args}
            )
            if not decision.allow:
                return ToolResult(False, f"Blocked by hook: {decision.reason}")

        result: ToolResult | None = None
        for source in self.sources:
            result = source.dispatch(name, args)
            if result is not None:
                break
        if result is None:
            result = ToolResult(False, f"Error: unknown tool {name!r}")

        if self.hooks is not None:
            event = (
                HookEvent.POST_TOOL_USE
                if result.ok
                else HookEvent.POST_TOOL_USE_FAILURE
            )
            self.hooks.fire(event, {"tool_name": name, "tool_result": result.content})
        return result

    def fire_hook(self, event: HookEvent | str, payload: dict | None = None) -> HookDecision:
        if self.hooks is None:
            return HookDecision()
        return self.hooks.fire(event, payload or {})
