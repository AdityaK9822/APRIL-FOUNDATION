"""A file workspace for the native proposer."""

from __future__ import annotations

import shutil
from pathlib import Path

from ..tools.base import ToolResult, ToolSchema
from ..tools.builtin.files import FilesTool
from ..tools.registry import Toolset


class WorkspaceSource:
    name = "workspace"

    def __init__(self, root: str | Path):
        self.files = FilesTool(root)

    def schemas(self) -> list[ToolSchema]:
        return self.files.schemas()

    def dispatch(self, name: str, args: dict) -> ToolResult | None:
        return self.files.dispatch(name, args)

    def context(self) -> str:
        return ""


class Workspace:
    """A temp directory with read/write/list tools and a harvest helper."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.source = WorkspaceSource(self.root)
        self.toolset = Toolset([self.source])

    def harvest(self, subpath: str, dest: str | Path) -> Path | None:
        source = self.root / subpath
        if not source.is_dir():
            return None
        target = Path(dest)
        target.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, target, dirs_exist_ok=True)
        return target
