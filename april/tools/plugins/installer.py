"""Plugin install/uninstall and a local marketplace index.

Sources: a plugin directory, a ``.zip`` archive, or an http(s) URL to one.
Zip entries that escape the destination are refused; the archive is size-capped.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import urllib.request
import uuid
import zipfile
from pathlib import Path

from .manifest import PluginManifest, discover_plugins, load_manifest

MAX_ARCHIVE_BYTES = 50 * 1024 * 1024


class InstallError(Exception):
    pass


def install(
    source: str | Path,
    root: str | Path,
    name: str | None = None,
    force: bool = False,
) -> PluginManifest:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        if isinstance(source, str) and source.startswith(("http://", "https://")):
            archive = base / "download.zip"
            with urllib.request.urlopen(source, timeout=30) as response:
                data = response.read(MAX_ARCHIVE_BYTES + 1)
            if len(data) > MAX_ARCHIVE_BYTES:
                raise InstallError("archive exceeds the size limit")
            archive.write_bytes(data)
            source_path = archive
        else:
            source_path = Path(source)

        if source_path.is_dir():
            plugin_root = find_plugin_root(source_path)
        elif source_path.is_file() and zipfile.is_zipfile(source_path):
            extract = base / "extract"
            extract.mkdir()
            safe_extract(source_path, extract)
            plugin_root = find_plugin_root(extract)
        else:
            raise InstallError("source must be a plugin directory, a zip, or an http(s) URL")

        manifest = load_manifest(plugin_root)
        if manifest is None:
            raise InstallError(
                "no .zcode-plugin/plugin.json (or compatible) manifest found"
            )

        target = root / (name or manifest.name)
        if target.exists() and not force:
            raise InstallError(f"plugin already installed at {target}")

        staging = root / f".tmp-{uuid.uuid4().hex[:8]}"
        try:
            shutil.copytree(plugin_root, staging)
            if target.exists():
                shutil.rmtree(target)
            os.replace(staging, target)
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)

        installed_manifest = load_manifest(target)
        if installed_manifest is None:
            raise InstallError("installed plugin failed manifest validation")
        return installed_manifest


def uninstall(name: str, root: str | Path) -> bool:
    root = Path(root).resolve()
    target = (root / name).resolve()
    if root != target and root not in target.parents:
        raise InstallError("refusing to remove a path outside the plugin root")
    if not target.is_dir():
        return False
    shutil.rmtree(target)
    return True


def installed(root: str | Path) -> list[PluginManifest]:
    root = Path(root)
    if not root.is_dir():
        return []
    return discover_plugins([path for path in root.iterdir() if path.is_dir()])


def find_plugin_root(path: Path) -> Path:
    candidates = [path]
    if path.is_dir():
        candidates.extend(child for child in path.iterdir() if child.is_dir())
    for candidate in candidates:
        if load_manifest(candidate) is not None:
            return candidate
    raise InstallError("no plugin manifest found in the source")


def safe_extract(archive: Path, dest: Path) -> None:
    """Extract a zip, refusing any member that escapes ``dest``."""
    dest = dest.resolve()
    with zipfile.ZipFile(archive) as zf:
        for member in zf.infolist():
            target = (dest / member.filename).resolve()
            if dest != target and dest not in target.parents:
                raise InstallError(f"zip entry escapes the target: {member.filename}")
        zf.extractall(dest)


class Marketplace:
    """A local JSON index: ``{"version": 1, "plugins": [{name, source, ...}]}``."""

    def __init__(self, index_path: str | Path):
        self.index_path = Path(index_path)

    def entries(self) -> list[dict]:
        if not self.index_path.is_file():
            return []
        try:
            doc = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise InstallError(f"marketplace index unreadable: {exc}") from exc
        plugins = doc.get("plugins") if isinstance(doc, dict) else None
        if not isinstance(plugins, list):
            raise InstallError("marketplace index must contain a 'plugins' list")
        return [entry for entry in plugins if isinstance(entry, dict)]

    def search(self, query: str = "") -> list[dict]:
        needle = query.lower().strip()
        return [
            entry
            for entry in self.entries()
            if not needle
            or needle in str(entry.get("name", "")).lower()
            or needle in str(entry.get("description", "")).lower()
        ]

    def get(self, name: str) -> dict | None:
        for entry in self.entries():
            if str(entry.get("name", "")) == name:
                return entry
        return None

    def install(self, name: str, root: str | Path, force: bool = False) -> PluginManifest:
        entry = self.get(name)
        if entry is None:
            raise InstallError(f"unknown marketplace plugin: {name!r}")
        source = str(entry.get("source") or "")
        if not source:
            raise InstallError(f"marketplace entry {name!r} has no source")
        return install(
            source,
            root,
            name=str(entry.get("install_as") or name),
            force=force,
        )
