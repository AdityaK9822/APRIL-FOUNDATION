"""McpSource: the MCP runtime behind the ToolSource seam."""

from __future__ import annotations

from ..base import ToolResult, ToolSchema
from .client import McpRuntime
from .registry import McpRegistry


class McpSource:
    name = "mcp"

    def __init__(self, registry: McpRegistry | None = None):
        self.runtime = McpRuntime(registry=registry)

    def start(self) -> None:
        self.runtime.start()

    def refresh(self) -> None:
        self.runtime.refresh()

    def close(self) -> None:
        self.runtime.close()

    def schemas(self) -> list[ToolSchema]:
        return [
            ToolSchema(
                name=item["function"]["name"],
                description=item["function"].get("description") or "",
                parameters=item["function"].get("parameters") or {},
            )
            for item in self.runtime.tool_schemas()
        ]

    def dispatch(self, name: str, args: dict) -> ToolResult | None:
        output = self.runtime.dispatch(name, dict(args or {}))
        if output is None:
            return None
        return ToolResult(not output.startswith("Error:"), output)

    def context(self) -> str:
        status = self.runtime.status()
        connected = [
            server["name"]
            for server in status["servers"]
            if server.get("status") == "connected"
        ]
        if not connected:
            return ""
        return "MCP servers connected: " + ", ".join(connected)

    def status(self) -> dict:
        return self.runtime.status()
