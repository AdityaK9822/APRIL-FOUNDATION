"""The Learnable seam: one interface, three targets.

``edit`` is a pure preview; ``commit`` applies the winner. The workflow target
delegates validation and rollouts to the ported harness adapter; memory and
device are self-contained.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from ..memory.base import MemoryInput, MemoryStore
from . import tree as harness_tree
from .substrate import APRIL_SURFACE


@dataclass(frozen=True)
class Trace:
    """The evidence one learning run is allowed to see."""

    task_id: str = ""
    run_id: str = ""
    session_id: str = ""
    evidence: str = ""


@runtime_checkable
class Learnable(Protocol):
    name: str

    def read(self) -> dict: ...

    def edit(self, proposal: dict) -> dict: ...

    def validate(self, snapshot: dict) -> list[str]: ...

    def evaluate(self, snapshot: dict, trace: Trace) -> float: ...

    def commit(self, snapshot: dict) -> None: ...

    def render(self, snapshot: dict) -> str: ...

    def proposal_schema(self) -> str: ...


class WorkflowTarget:
    """A harness tree behind the ``AgentAdapter`` seam."""

    name = "workflow"

    def __init__(self, adapter, surface=None):
        self.adapter = adapter
        self.surface = surface or (
            adapter.substrate.surface()
            if hasattr(adapter, "substrate")
            else APRIL_SURFACE
        )
        self._current: dict | None = None

    def read(self) -> dict:
        if self._current is not None:
            return self._current
        tree = harness_tree.read_tree(self.surface, self.adapter.base_harness())
        return {"files": dict(tree)}

    def edit(self, proposal: dict) -> dict:
        current = dict(self.read()["files"])
        for rel, content in (proposal.get("files") or {}).items():
            rel = str(rel).replace("\\", "/").strip("/")
            if content == "":
                current.pop(rel, None)
            else:
                current[rel] = str(content)
        return {"files": current}

    def validate(self, snapshot: dict) -> list[str]:
        problems: list[str] = []
        try:
            harness_tree.validate(
                self.surface,
                snapshot["files"],
                allow_subagents=bool(getattr(self.adapter.spec, "allow_subagents", False)),
                reserved_scripts=harness_tree.RESERVED_SCRIPTS
                | set(getattr(self.adapter.spec, "reserved_scripts", frozenset())),
            )
        except Exception as exc:
            problems.append(str(exc))
        try:
            self.adapter.validate_tree(snapshot["files"])
        except Exception as exc:
            problems.append(str(exc))
        return problems

    def evaluate(self, snapshot: dict, trace: Trace) -> float:
        if not trace.task_id:
            return 0.0
        tmp = Path(tempfile.mkdtemp(prefix="april-eval-"))
        try:
            harness_dir = harness_tree.write_tree(
                self.surface, snapshot["files"], tmp / "harness"
            )
            run_dir = self.adapter.execute(
                trace.task_id,
                harness_dir,
                trace.run_id or f"eval-{uuid.uuid4().hex[:8]}",
            )
            return 1.0 if self.adapter.delivered(run_dir) else 0.0
        except Exception:
            return 0.0
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def commit(self, snapshot: dict) -> None:
        self._current = snapshot

    def render(self, snapshot: dict) -> str:
        return "\n\n".join(
            f"### {rel}\n{content}" for rel, content in sorted(snapshot["files"].items())
        )

    def proposal_schema(self) -> str:
        return '{"files": {"relative/path": "full file content"}}'


class MemoryTarget:
    """Structured memory entries behind the ``MemoryStore`` seam."""

    name = "memory"

    def __init__(self, store: MemoryStore, session_id: str = "", limit: int = 50):
        self.store = store
        self.session_id = session_id
        self.limit = limit

    def read(self) -> dict:
        entries = [
            str(memory.content.get("text") or "")
            for memory in self.store.recall("", k=self.limit)
        ]
        return {"entries": [entry for entry in entries if entry.strip()]}

    def edit(self, proposal: dict) -> dict:
        current = list(self.read()["entries"])
        for entry in proposal.get("entries") or []:
            text = str(entry).strip()
            if text and text not in current:
                current.append(text)
        return {"entries": current}

    def validate(self, snapshot: dict) -> list[str]:
        entries = snapshot.get("entries") or []
        return [
            f"entry {index} is empty"
            for index, entry in enumerate(entries)
            if not str(entry).strip()
        ]

    def evaluate(self, snapshot: dict, trace: Trace) -> float:
        return 1.0 if snapshot.get("entries") else 0.0

    def commit(self, snapshot: dict) -> None:
        before = set(self.read()["entries"])
        for entry in snapshot.get("entries") or []:
            if entry not in before:
                self.store.add_memory(
                    MemoryInput(
                        memory_type="fact",
                        content={"text": entry},
                        session_id=self.session_id,
                    )
                )

    def render(self, snapshot: dict) -> str:
        return "\n".join(f"- {entry}" for entry in snapshot["entries"])

    def proposal_schema(self) -> str:
        return '{"entries": ["new durable fact"]}'


class DeviceTarget:
    """A device config JSON behind the telemetry seam."""

    name = "device"

    def __init__(self, path: str | Path, required_keys: tuple[str, ...] = ()):
        self.path = Path(path)
        self.required_keys = required_keys

    def read(self) -> dict:
        if not self.path.is_file():
            return {"config": {}}
        try:
            return {"config": json.loads(self.path.read_text(encoding="utf-8"))}
        except (OSError, json.JSONDecodeError):
            return {"config": {}}

    def edit(self, proposal: dict) -> dict:
        current = dict(self.read()["config"])
        current.update(proposal.get("config") or {})
        return {"config": current}

    def validate(self, snapshot: dict) -> list[str]:
        config = snapshot.get("config") or {}
        return [
            f"missing key: {key}" for key in self.required_keys if key not in config
        ]

    def evaluate(self, snapshot: dict, trace: Trace) -> float:
        return 1.0 if not self.validate(snapshot) else 0.0

    def commit(self, snapshot: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        staging = self.path.with_suffix(self.path.suffix + ".tmp")
        staging.write_text(json.dumps(snapshot["config"], indent=2), encoding="utf-8")
        os.replace(staging, self.path)

    def render(self, snapshot: dict) -> str:
        return json.dumps(snapshot["config"], indent=2)

    def proposal_schema(self) -> str:
        return '{"config": {"key": "value"}}'
