"""Native proposer: an agentic file-editing session over the Model seam.

Replaces the old claude/opencode/zcode subprocess channels. The model reads the
evidence tree, edits ``context/harness/``, and the edited tree is harvested.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from ..llm import ChatRequest, Model
from .workspace import Workspace


class ProposerError(RuntimeError):
    def __init__(self, message: str, *, attempts: int = 0, cost_usd: float = 0.0):
        super().__init__(message)
        self.attempts = attempts
        self.cost_usd = cost_usd


@dataclass
class Reply:
    label: str
    text: str
    ok: bool
    cost_usd: float = 0.0
    wall_s: float = 0.0
    attempts: int = 0
    cached: bool = False
    channel: str = "native"
    error: str = ""
    audit: Path | None = None
    workspace: Path | None = None

    def __bool__(self) -> bool:
        return self.ok


class Proposer:
    def __init__(
        self,
        model: Model,
        model_name: str,
        *,
        audit_dir: str | Path | None = None,
        attempts: int = 2,
        max_turns: int = 12,
        temperature: float = 0.4,
    ):
        self.model = model
        self.model_name = model_name
        self.audit_dir = Path(audit_dir) if audit_dir else None
        self.attempts = max(1, int(attempts))
        self.max_turns = max(1, int(max_turns))
        self.temperature = float(temperature)

    def call(
        self,
        mission: str,
        context_dir: str | Path | None = None,
        label: str = "",
        *,
        system: str = "",
        timeout: int | None = None,
        max_attempts: int | None = None,
        harvest: str | None = None,
    ) -> Reply:
        cache_key = _fingerprint(label, mission, system, context_dir)
        cached = self._cached(label, cache_key, require_workspace=bool(harvest))
        if cached is not None:
            return cached

        tries = max(1, int(self.attempts if max_attempts is None else max_attempts))
        last = ""
        started = time.time()
        for attempt in range(1, tries + 1):
            tmp = Path(tempfile.mkdtemp(prefix="april-prop-"))
            try:
                if context_dir and Path(context_dir).is_dir():
                    shutil.copytree(context_dir, tmp / "context", dirs_exist_ok=True)
                (tmp / "output").mkdir(exist_ok=True)
                workspace = Workspace(tmp)
                use_tools = context_dir is not None or bool(harvest)
                messages = self._messages(mission, system, use_tools)
                text = self._session(workspace, messages, use_tools=use_tools)
                reply = Reply(
                    label=label,
                    text=text,
                    ok=True,
                    wall_s=round(time.time() - started, 1),
                    attempts=attempt,
                    channel="native",
                )
                if harvest:
                    reply.workspace = self._harvest(tmp, harvest, label, cache_key)
                self._write_audit(reply, mission, system, cache_key)
                return reply
            except Exception as exc:
                last = f"{type(exc).__name__}: {exc}"
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        raise ProposerError(
            f"[{label}] native/{self.model_name}: {last}", attempts=tries
        )

    def _session(self, workspace: Workspace, messages: list[dict], *, use_tools: bool) -> str:
        if not use_tools:
            reply = self.model.complete(
                ChatRequest(
                    model=self.model_name,
                    messages=messages,
                    temperature=self.temperature,
                )
            )
            return reply.text
        for _ in range(self.max_turns):
            reply = self.model.complete(
                ChatRequest(
                    model=self.model_name,
                    messages=messages,
                    tools=workspace.toolset.to_openai(),
                    temperature=self.temperature,
                )
            )
            if not reply.tool_calls:
                return reply.text
            messages.append(
                {
                    "role": "assistant",
                    "content": reply.text,
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": call.name,
                                "arguments": call.arguments,
                            },
                        }
                        for call in reply.tool_calls
                    ],
                }
            )
            for call in reply.tool_calls:
                try:
                    args = json.loads(call.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = workspace.toolset.dispatch(call.name, args)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": result.content,
                    }
                )
        return ""

    def _messages(self, mission: str, system: str, use_tools: bool) -> list[dict]:
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        if use_tools:
            intro = (
                "You are editing a harness in a file workspace. Evidence is under "
                "`context/`. Edit the harness under `context/harness/` and write "
                "your final answer to `output/answer.md`. Use read_file, write_file "
                "and list_dir with paths relative to the workspace root."
            )
            messages.append({"role": "user", "content": f"{intro}\n\n{mission}"})
        else:
            messages.append({"role": "user", "content": mission})
        return messages

    # -- cache and audit ----------------------------------------------------

    def _paths(self, label: str, cache_key: str = "") -> dict[str, Path] | None:
        if self.audit_dir is None:
            return None
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", label)
        if cache_key:
            safe = f"{safe}.{cache_key}"
        directory = self.audit_dir
        directory.mkdir(parents=True, exist_ok=True)
        return {
            "mission": directory / f"{safe}.mission.md",
            "system": directory / f"{safe}.system.md",
            "response": directory / f"{safe}.response.md",
            "meta": directory / f"{safe}.meta.json",
            "workspace": directory / f"{safe}.workspace",
        }

    def _cached(
        self, label: str, cache_key: str = "", *, require_workspace: bool = False
    ) -> Reply | None:
        paths = self._paths(label, cache_key)
        if not paths or not paths["response"].is_file():
            return None
        if require_workspace and not paths["workspace"].is_dir():
            return None
        text = paths["response"].read_text(encoding="utf-8", errors="replace")
        if not text.strip():
            return None
        return Reply(
            label=label,
            text=text,
            ok=True,
            cached=True,
            channel="native",
            audit=paths["response"],
            workspace=paths["workspace"] if paths["workspace"].is_dir() else None,
        )

    def _write_audit(
        self, reply: Reply, mission: str, system: str, cache_key: str = ""
    ) -> None:
        paths = self._paths(reply.label, cache_key)
        if not paths:
            return
        paths["mission"].write_text(mission, encoding="utf-8")
        if system:
            paths["system"].write_text(system, encoding="utf-8")
        paths["response"].write_text(reply.text, encoding="utf-8")
        paths["meta"].write_text(
            json.dumps(
                {
                    "label": reply.label,
                    "ok": reply.ok,
                    "channel": reply.channel,
                    "model": self.model_name,
                    "attempts": reply.attempts,
                    "wall_s": reply.wall_s,
                    "cached": reply.cached,
                    "error": reply.error,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        reply.audit = paths["response"]

    def _harvest(
        self, tmp: Path, sub: str, label: str, cache_key: str = ""
    ) -> Path | None:
        source = tmp / sub
        if not source.is_dir():
            return None
        paths = self._paths(label, cache_key)
        target = (
            paths["workspace"]
            if paths
            else Path(tempfile.mkdtemp(prefix="april-harvest-"))
        )
        if paths:
            shutil.rmtree(target, ignore_errors=True)
        return Workspace(tmp).harvest(sub, target)


class DryProposer:
    """Offline stand-in: every call fails closed, so stages inherit the base."""

    def call(self, *args, **kwargs) -> Reply:
        raise ProposerError("dry: no model attached")


def _fingerprint(
    label: str, mission: str, system: str, context_dir: str | Path | None
) -> str:
    digest = hashlib.sha256()
    for value in (label, mission, system):
        digest.update(str(value).encode("utf-8", "replace"))
        digest.update(b"\0")
    if context_dir:
        root = Path(context_dir)
        digest.update(str(root.resolve()).encode("utf-8", "replace"))
        digest.update(b"\0")
        if root.is_dir():
            for path in sorted(root.rglob("*")):
                if not path.is_file():
                    continue
                digest.update(str(path.relative_to(root)).encode("utf-8", "replace"))
                digest.update(b"\0")
                digest.update(path.read_bytes())
    return digest.hexdigest()[:16]
