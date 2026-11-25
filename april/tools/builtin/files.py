"""Workspace-scoped file tools. Paths that escape the workspace are refused."""

from __future__ import annotations

from pathlib import Path

from ..base import ToolResult, ToolSchema


class FilesTool:
    def __init__(self, workspace: str | Path | None = None):
        self.workspace = Path(workspace or ".").resolve()

    def schemas(self) -> list[ToolSchema]:
        return [
            ToolSchema(
                name="read_file",
                description="Read a text file inside the workspace.",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            ),
            ToolSchema(
                name="write_file",
                description="Write a text file inside the workspace.",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                },
            ),
            ToolSchema(
                name="list_dir",
                description="List entries of a directory inside the workspace.",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            ),
        ]

    def dispatch(self, name: str, args: dict) -> ToolResult | None:
        if name not in {"read_file", "write_file", "list_dir"}:
            return None
        try:
            path = self._resolve(str(args.get("path") or ""))
        except ValueError as exc:
            return ToolResult(False, f"Error: {exc}")
        try:
            if name == "read_file":
                return ToolResult(True, path.read_text(encoding="utf-8"))
            if name == "write_file":
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(str(args.get("content") or ""), encoding="utf-8")
                return ToolResult(True, f"wrote {path.relative_to(self.workspace)}")
            entries = sorted(p.name + ("/" if p.is_dir() else "") for p in path.iterdir())
            return ToolResult(True, "\n".join(entries))
        except OSError as exc:
            return ToolResult(False, f"Error: {exc}")

    def _resolve(self, rel: str) -> Path:
        if not rel.strip():
            raise ValueError("path is required")
        path = (self.workspace / rel).resolve()
        if self.workspace != path and self.workspace not in path.parents:
            raise ValueError(f"path escapes the workspace: {rel}")
        return path
