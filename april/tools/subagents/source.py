"""The subagent tool: one ``run_subagent`` call per delegation."""

from __future__ import annotations

from ..base import ToolResult, ToolSchema
from .profile import SubagentProfile
from .runner import SubagentRunner


class SubagentsSource:
    name = "subagents"

    def __init__(self, profiles: list[SubagentProfile], runner: SubagentRunner):
        self.profiles = {profile.name: profile for profile in profiles}
        self.runner = runner

    def schemas(self) -> list[ToolSchema]:
        return [
            ToolSchema(
                name="run_subagent",
                description=(
                    "Delegate a task to a specialist subagent. Use when the task "
                    "matches a subagent's description."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Subagent name."},
                        "task": {"type": "string", "description": "The task to run."},
                    },
                    "required": ["name", "task"],
                },
            )
        ]

    def dispatch(self, name: str, args: dict) -> ToolResult | None:
        if name != "run_subagent":
            return None
        profile = self.profiles.get(str(args.get("name") or ""))
        if profile is None:
            return ToolResult(False, f"Error: unknown subagent {args.get('name')!r}")
        task = str(args.get("task") or "")
        if not task.strip():
            return ToolResult(False, "Error: task is required")
        return ToolResult(True, self.runner.run(profile, task))

    def context(self) -> str:
        if not self.profiles:
            return ""
        lines = [
            "Subagents available — call `run_subagent` when the task matches:"
        ]
        for profile in sorted(self.profiles.values(), key=lambda item: item.name):
            lines.append(f"- `{profile.name}` [{profile.source}]: {profile.description}")
        return "\n".join(lines)
