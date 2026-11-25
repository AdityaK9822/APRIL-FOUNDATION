"""Shell execution, ported from ``tools/shell.py`` onto the tool seam."""

from __future__ import annotations

import subprocess

from ..base import ToolResult, ToolSchema


class ShellTool:
    name = "shell_execute"

    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=(
                "Execute a shell command on the host system. Use for file operations, "
                "system commands, and other shell tasks."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute (e.g., 'ls -la').",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds (default: 30).",
                        "default": 30,
                    },
                },
                "required": ["command"],
            },
        )

    def dispatch(self, args: dict) -> ToolResult:
        command = str(args.get("command") or "")
        if not command.strip():
            return ToolResult(False, "Error: command is required")
        try:
            timeout = int(args.get("timeout") or 30)
        except (TypeError, ValueError):
            timeout = 30
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(False, f"Error: command timed out after {timeout} seconds")
        except Exception as exc:
            return ToolResult(False, f"Error: execution failed: {exc}")
        if result.returncode == 0:
            return ToolResult(True, result.stdout)
        return ToolResult(
            False,
            f"Error: {result.stderr.strip() or f'exit {result.returncode}'}",
        )
