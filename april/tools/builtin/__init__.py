"""Built-in tools: shell + workspace files, one source."""

from __future__ import annotations

from pathlib import Path

from ..base import ToolResult, ToolSchema
from .files import FilesTool
from .shell import ShellTool


class BuiltinSource:
    name = "builtin"

    def __init__(self, workspace: str | Path | None = None):
        self.shell = ShellTool()
        self.files = FilesTool(workspace)

    def schemas(self) -> list[ToolSchema]:
        return [self.shell.schema(), *self.files.schemas()]

    def dispatch(self, name: str, args: dict) -> ToolResult | None:
        if name == self.shell.name:
            return self.shell.dispatch(args)
        return self.files.dispatch(name, args)

    def context(self) -> str:
        return ""
