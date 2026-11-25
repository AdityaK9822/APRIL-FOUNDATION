"""Hook configuration and trust admission.

Workspace hooks are untrusted by default (ZCode docs 09); only roots named in
the trust policy load unless ``APRIL_TRUST_WORKSPACE_HOOKS=1``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from ..base import HOOK_EVENTS


@dataclass(frozen=True)
class HookSpec:
    event: str
    command: str
    args: tuple[str, ...] = ()
    kind: str = "command"
    matcher: str = ""
    timeout: float = 60.0
    on_error: str = "allow"
    async_: bool = False


@dataclass
class HookSet:
    hooks: list[HookSpec] = field(default_factory=list)
    trusted: bool = True
    reason: str = ""


class TrustPolicy:
    """A path is trusted when it sits under a trusted root."""

    def __init__(
        self,
        trusted_roots: list[str | Path] | None = None,
        allow_workspace: bool | None = None,
    ):
        self.trusted_roots = [Path(root).resolve() for root in (trusted_roots or [])]
        if allow_workspace is None:
            allow_workspace = os.getenv("APRIL_TRUST_WORKSPACE_HOOKS", "0").strip() == "1"
        self.allow_workspace = bool(allow_workspace)

    def is_trusted(self, path: str | Path) -> bool:
        if self.allow_workspace:
            return True
        resolved = Path(path).resolve()
        return any(
            root == resolved or root in resolved.parents for root in self.trusted_roots
        )


def load_hooks(path: str | Path, trust: TrustPolicy | None = None) -> HookSet:
    path = Path(path)
    if trust is not None and not trust.is_trusted(path):
        return HookSet([], trusted=False, reason=f"untrusted hook source skipped: {path}")
    if not path.is_file():
        return HookSet([], trusted=True)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return HookSet([], trusted=True, reason=f"hooks unreadable: {exc}")
    config = doc
    if isinstance(doc, dict) and isinstance(doc.get("hooks"), dict):
        config = doc["hooks"]
    if not isinstance(config, dict):
        return HookSet([], trusted=True, reason="hooks config is not an object")
    if not config.get("enabled", True):
        return HookSet([], trusted=True, reason="hooks disabled")
    events = config.get("events")
    if not isinstance(events, dict):
        return HookSet([], trusted=True, reason="hooks config has no events")

    hooks: list[HookSpec] = []
    for event, entries in events.items():
        if event not in HOOK_EVENTS or not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            matcher = str(entry.get("matcher") or "")
            specs = entry.get("hooks")
            if not isinstance(specs, list):
                continue
            for spec in specs:
                if not isinstance(spec, dict):
                    continue
                command = str(spec.get("command") or "").strip()
                if not command:
                    continue
                kind = str(
                    spec.get("type") or ("process" if spec.get("args") else "command")
                )
                timeout = spec.get("timeoutMs") or spec.get("timeout") or 60.0
                if spec.get("timeoutMs"):
                    timeout = float(timeout) / 1000.0
                hooks.append(
                    HookSpec(
                        event=event,
                        command=command,
                        args=tuple(str(a) for a in (spec.get("args") or [])),
                        kind=kind,
                        matcher=matcher,
                        timeout=float(timeout),
                        on_error=str(spec.get("onError") or "allow"),
                        async_=bool(spec.get("async")),
                    )
                )
    return HookSet(hooks, trusted=True)
