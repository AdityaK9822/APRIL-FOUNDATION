"""MCP server registry — the config surface for Model Context Protocol servers.

This is the *registry only*: name/transport/command/url/env/enabled records on
disk that a future MCP runtime client (docs/backend-adapters/02) would read.
It deliberately does NOT spawn processes or talk the MCP protocol — the web UI
(/api/mcp/*, P8 wiring) gets real CRUD against this file, and "Test" reports
validation only.

Storage: one JSON document at `.ade/mcp_servers.json` (git-ignored runtime
state) so servers added through the UI survive restarts without any of it
being committed. Path resolution:

1. `APRIL_MCP_REGISTRY` — explicit path override;
2. `APRIL_ADE_ROOT/mcp_servers.json`;
3. `.ade/mcp_servers.json`.

Validation follows the profile-loader philosophy: unknown top-level fields are
refused loudly (a typo can never silently compile nothing), transport is one of
{"stdio", "http"}, stdio requires a command, http requires an http(s) URL.

# ponytail: the registry is a single-user local file with a process lock —
no multi-writer story. Ceiling: concurrent frontends on one backend.
"""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .oauth import validate_oauth

TRANSPORTS = ("stdio", "http")
REGISTRY_VERSION = 1

_SLUG_RX = re.compile(r"[^a-z0-9._-]+")


class RegistryError(Exception):
    """Base: a registry operation was refused. str(e) is UI-presentable."""


class UnknownServer(RegistryError):
    pass


class DuplicateName(RegistryError):
    pass


def slugify(name: str) -> str:
    slug = _SLUG_RX.sub("-", str(name or "").strip().lower())
    slug = re.sub(r"-{2,}", "-", slug).strip("-.")
    return slug


def validate_server(record: Dict[str, Any]) -> List[str]:
    """Return the list of validation problems for one server record (pure)."""
    errors: List[str] = []
    known = {"id", "name", "transport", "command", "args", "url", "env",
             "enabled", "created_at", "updated_at", "oauth"}
    unknown = sorted(set(record) - known)
    if unknown:
        errors.append(f"unknown field(s): {', '.join(unknown)}")
    name = str(record.get("name") or "").strip()
    if not name:
        errors.append("name is required")
    elif "/" in name or "\\" in name or name.startswith("."):
        errors.append("name must not contain path separators or start with '.'")
    if record.get("transport") not in TRANSPORTS:
        errors.append(f"transport must be one of {TRANSPORTS}")
    transport = record.get("transport")
    if transport == "stdio":
        if not str(record.get("command") or "").strip():
            errors.append("stdio transport requires a command")
        args = record.get("args")
        if args is not None and not (
            isinstance(args, list) and all(isinstance(a, str) for a in args)
        ):
            errors.append("args must be a list of strings")
    elif transport == "http":
        url = str(record.get("url") or "").strip()
        if not url:
            errors.append("http transport requires a url")
        elif not (url.startswith("http://") or url.startswith("https://")):
            errors.append("url must start with http:// or https://")
    env = record.get("env")
    if env is not None:
        if not isinstance(env, dict) or not all(
            isinstance(k, str) and k.strip() and isinstance(v, str)
            for k, v in env.items()
        ):
            errors.append("env must be a mapping of non-empty string keys to strings")
    if "enabled" in record and not isinstance(record["enabled"], bool):
        errors.append("enabled must be a boolean")
    oauth = record.get("oauth")
    if oauth is not None:
        errors.extend(validate_oauth(oauth))
    return errors


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class McpRegistry:
    """CRUD over the on-disk MCP server registry. Sync, process-local locked."""

    def __init__(self, path: Optional[Path | str] = None) -> None:
        self.path = Path(
            path
            if path is not None
            else os.getenv("APRIL_MCP_REGISTRY", "").strip()
            or _default_registry_path()
        )
        self._lock = threading.RLock()

    # -- disk ----------------------------------------------------------------

    def _read(self) -> Dict[str, Any]:
        if not self.path.is_file():
            return {"version": REGISTRY_VERSION, "servers": []}
        try:
            doc = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RegistryError(f"registry file unreadable ({self.path}): {exc}") from exc
        if not isinstance(doc, dict) or "servers" not in doc:
            raise RegistryError(
                f"registry file corrupt ({self.path}): expected an object with 'servers'"
            )
        servers = doc.get("servers")
        if not isinstance(servers, list):
            raise RegistryError(
                f"registry file corrupt ({self.path}): 'servers' must be a list"
            )
        for srv in servers:
            if not isinstance(srv, dict):
                raise RegistryError(
                    f"registry file corrupt ({self.path}): server entries must be objects"
                )
        return {"version": doc.get("version", REGISTRY_VERSION), "servers": servers}

    def _write(self, doc: Dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)  # atomic on POSIX

    # -- CRUD ----------------------------------------------------------------

    def list(self) -> List[Dict[str, Any]]:
        with self._lock:
            return sorted(
                self._read()["servers"],
                key=lambda s: (str(s.get("name", "")).lower(), s.get("id", "")),
            )

    def get(self, server_id: str) -> Dict[str, Any]:
        with self._lock:
            for srv in self._read()["servers"]:
                if srv.get("id") == server_id:
                    return srv
        raise UnknownServer(f"unknown MCP server id: {server_id!r}")

    def add(
        self,
        *,
        name: str,
        transport: Optional[str] = None,
        command: Optional[str] = None,
        args: Optional[List[str]] = None,
        url: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        enabled: bool = True,
    ) -> Dict[str, Any]:
        name = str(name or "").strip()
        record: Dict[str, Any] = {
            "name": name,
            "transport": transport,
            "enabled": enabled,
        }
        if command is not None:
            record["command"] = command
        if args is not None:
            record["args"] = list(args)
        if url is not None:
            record["url"] = url
        if env:
            record["env"] = dict(env)
        errors = validate_server(record)
        if errors:
            raise RegistryError("; ".join(errors))
        sid = slugify(name)
        if not sid:
            raise RegistryError("name must contain at least one [a-z0-9] character")
        record["id"] = sid
        record["created_at"] = record["updated_at"] = _now()
        with self._lock:
            doc = self._read()
            existing = [str(s.get("name", "")).strip() for s in doc["servers"]]
            if name in existing:
                raise DuplicateName(f"an MCP server named {name!r} already exists")
            if any(s.get("id") == sid for s in doc["servers"]):
                raise DuplicateName(f"server id {sid!r} (slug of another name) already exists")
            doc["servers"].append(record)
            self._write(doc)
        return record

    def update(self, server_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
        allowed = {"name", "transport", "command", "args", "url", "env", "enabled"}
        unknown = sorted(set(patch) - allowed)
        if unknown:
            raise RegistryError(f"unknown field(s): {', '.join(unknown)}")
        with self._lock:
            doc = self._read()
            for srv in doc["servers"]:
                if srv.get("id") == server_id:
                    merged = {**srv, **patch}
                    errors = validate_server(merged)
                    if errors:
                        raise RegistryError("; ".join(errors))
                    new_id = merged["name"] != srv.get("name")
                    if new_id:
                        sid = slugify(merged["name"])
                        if not sid:
                            raise RegistryError("name must contain at least one [a-z0-9] character")
                        clash = [
                            s
                            for s in doc["servers"]
                            if s.get("id") != server_id
                            and (
                                s.get("id") == sid
                                or str(s.get("name", "")).strip() == merged["name"]
                            )
                        ]
                        if clash:
                            raise DuplicateName(
                                f"an MCP server named {merged['name']!r} already exists"
                            )
                        merged["id"] = sid
                    merged["updated_at"] = _now()
                    doc["servers"] = [
                        merged if s.get("id") == server_id else s
                        for s in doc["servers"]
                    ]
                    self._write(doc)
                    return merged
        raise UnknownServer(f"unknown MCP server id: {server_id!r}")

    def delete(self, server_id: str) -> bool:
        with self._lock:
            doc = self._read()
            before = len(doc["servers"])
            doc["servers"] = [s for s in doc["servers"] if s.get("id") != server_id]
            if len(doc["servers"]) == before:
                raise UnknownServer(f"unknown MCP server id: {server_id!r}")
            self._write(doc)
        return True

    def test(self, server_id: str, probe: bool | None = None) -> Dict[str, Any]:
        """Validate a record, then (unless probing is disabled) attempt connect."""
        srv = self.get(server_id)
        errors = validate_server(srv)
        report = {
            "id": srv["id"],
            "name": srv["name"],
            "ok": not errors,
            "errors": errors,
            "client": "not-connected",
            "reason": "validated; probing disabled (set APRIL_MCP_PROBE=0 behavior)",
            "protocol_version": "",
            "tools": [],
        }
        if errors or (probe is False):
            return report
        if probe is None:
            probe = os.getenv("APRIL_MCP_PROBE", "1").strip() != "0"
        if not probe:
            return report
        from .client import probe_server

        attempt = probe_server(srv)
        report.update({
            "ok": bool(attempt.get("ok")),
            "client": "probe_server",
            "reason": str(attempt.get("error") or "connected"),
            "protocol_version": attempt.get("protocol_version", ""),
            "tools": attempt.get("tools", []),
        })
        return report

    # -- house style: runnable check -----------------------------------------


def demo() -> None:
    """Assert-shaped self-check, runnable offline: `python3 -m core.mcp_registry`."""
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        reg = McpRegistry(Path(td) / "mcp_servers.json")

        assert reg.list() == [], "fresh registry is empty"
        rec = reg.add(name="Local Shell", transport="stdio", command="python", args=["-m", "x"])
        assert rec["id"] == "local-shell" and rec["enabled"] is True

        # validation is loud (shape cases via the pure validator, add-path cases via add)
        for record, fragment in (
            ({"name": "x", "transport": "tcp"}, "transport"),
            ({"name": "x", "transport": "stdio"}, "command"),
            ({"name": "x", "transport": "http"}, "url"),
            ({"name": "x", "transport": "http", "url": "ftp://y"}, "url"),
            ({"name": "", "transport": "http", "url": "http://x"}, "name"),
        ):
            errors = validate_server(record)
            assert any(fragment in e for e in errors), (record, errors)

        # unknown fields refuse loudly, duplicates refuse
        try:
            reg.update(rec["id"], {"nonsense": 1})
            raise AssertionError("expected unknown-field refusal")
        except RegistryError:
            pass
        try:
            reg.update("nope", {"enabled": False})
            raise AssertionError("expected unknown id")
        except UnknownServer:
            pass

        upd = reg.update(rec["id"], {"enabled": False})
        assert upd["enabled"] is False and rec["id"] == upd["id"]

        # persistence across instances
        reg2 = McpRegistry(reg.path)
        assert [s["id"] for s in reg2.list()] == ["local-shell"]

        t = reg2.test("local-shell", probe=False)
        assert t["ok"] is True and t["client"] == "not-connected" and t["tools"] == []
        try:
            reg2.delete("missing")
            raise AssertionError("expected unknown id")
        except UnknownServer:
            pass
        assert reg2.delete("local-shell") is True and reg2.list() == []
    print("ok: mcp registry demo")


def _default_registry_path() -> Path:
    root = os.getenv("APRIL_ADE_ROOT", "").strip()
    return (Path(root) if root else Path(".ade")) / "mcp_servers.json"


if __name__ == "__main__":
    demo()
