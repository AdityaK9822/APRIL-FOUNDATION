"""Plugin manifests and the component catalog they contribute.

Loader only: a manifest names skills/agents/hooks/MCP servers and this module
turns those into namespaced roots. Bundled marketplace plugins are reference.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

MANIFESTS = (
    ".zcode-plugin/plugin.json",
    ".claude-plugin/plugin.json",
    ".codex-plugin/plugin.json",
)
UNSUPPORTED = {"channels", "lspServers", "outputStyles", "settings"}


@dataclass(frozen=True)
class PluginManifest:
    name: str
    root: Path
    description: str = ""
    version: str = ""
    mcp_servers: dict = field(default_factory=dict)
    skills: tuple[str, ...] = ()
    hooks: tuple[str, ...] = ()
    agents: tuple[str, ...] = ()
    commands: tuple[str, ...] = ()
    user_config: dict = field(default_factory=dict)
    dependencies: tuple[str, ...] = ()
    unsupported: tuple[str, ...] = ()

    def namespace(self, kind: str) -> str:
        return f"plugin:{self.name}:{kind}"


def load_manifest(root: str | Path) -> PluginManifest | None:
    root = Path(root)
    for rel in MANIFESTS:
        path = root / rel
        if not path.is_file():
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(doc, dict):
            return None
        name = str(doc.get("name") or "").strip()
        if not name:
            return None
        return PluginManifest(
            name=name,
            root=root,
            description=str(doc.get("description") or ""),
            version=str(doc.get("version") or ""),
            mcp_servers=dict(doc.get("mcpServers") or {}),
            skills=_as_tuple(doc.get("skills")),
            hooks=_as_tuple(doc.get("hooks")),
            agents=_as_tuple(doc.get("agents")),
            commands=_as_tuple(doc.get("commands")),
            user_config=dict(doc.get("userConfig") or {}),
            dependencies=_as_tuple(doc.get("dependencies")),
            unsupported=tuple(sorted(set(doc) & UNSUPPORTED)),
        )
    return None


def discover_plugins(roots: list[str | Path]) -> list[PluginManifest]:
    found: dict[str, PluginManifest] = {}
    for root in roots:
        manifest = load_manifest(root)
        if manifest is not None:
            found.setdefault(manifest.name, manifest)
    return sorted(found.values(), key=lambda manifest: manifest.name)


class PluginCatalog:
    """Component roots contributed by a set of manifests."""

    def __init__(self, plugins: list[PluginManifest]):
        self.plugins = list(plugins)

    def skill_roots(self) -> list[tuple[str, Path]]:
        return [
            (plugin.namespace("skills"), plugin.root / "skills")
            for plugin in self.plugins
            if (plugin.root / "skills").is_dir()
        ]

    def agent_roots(self) -> list[tuple[str, Path]]:
        return [
            (plugin.namespace("agents"), plugin.root / "agents")
            for plugin in self.plugins
            if (plugin.root / "agents").is_dir()
        ]

    def hook_paths(self) -> list[Path]:
        paths: list[Path] = []
        for plugin in self.plugins:
            for rel in plugin.hooks or ("hooks/hooks.json",):
                path = plugin.root / rel
                if path.is_file():
                    paths.append(path)
        return paths

    def mcp_servers(self) -> dict[str, dict]:
        servers: dict[str, dict] = {}
        for plugin in self.plugins:
            for name, record in plugin.mcp_servers.items():
                namespaced = f"plugin:{plugin.name}:{name}"
                servers[namespaced] = dict(record, name=namespaced)
        return servers


def _as_tuple(value) -> tuple[str, ...]:
    if isinstance(value, list):
        return tuple(str(item) for item in value)
    return ()
