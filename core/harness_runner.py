"""Web-facing driver for the Harness² engine: one run at a time, one human proposer.

This module owns no HTTP. `core/api.py` creates it, passes the event emitters, and
serializes its state. The engine itself is untouched: a run is exactly
`Harness(agent).run_task(...)` on a worker thread, with a unique tag so the
resume guard never skips a fresh web test.

Progress is observed from the run directory (run markers + base rollout dirs),
which is the same evidence the engine writes for any channel. A human proposal
parks the engine thread on a `threading.Event`; accept/decline releases it, and a
timeout releases it as a decline (the stage then falls back, ledger-exact).
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from .harness.execution_manager import Harness
from .harness.human_proposer import HumanProposer
from .harness.schedule import Namespace

_TERMINAL = {"ok", "skipped", "partial", "base-failed", "error", "cancelled"}
_SAFE_TAG = re.compile(r"^[A-Za-z0-9_-]+$")

EmitToolStart = Callable[[str, dict, str], None]
EmitToolDone = Callable[[str, str, str, bool], None]
EmitStatus = Callable[[str, dict], None]


def _noop_start(tool_name: str, args: dict, tool_id: str) -> None:  # pragma: no cover
    pass


def _noop_done(tool_name: str, result: str, tool_id: str, ok: bool) -> None:  # pragma: no cover
    pass


def _noop_status(status: str, metadata: dict) -> None:  # pragma: no cover
    pass


@dataclass
class Proposal:
    label: str
    mission: str
    files: dict[str, str]
    timeout_s: float
    created: float = field(default_factory=time.monotonic)
    decision: str = ""
    edited: set[str] = field(default_factory=set)
    event: threading.Event = field(default_factory=threading.Event)

    def public(self) -> dict:
        return {
            "label": self.label,
            "mission": self.mission,
            "files": dict(sorted(self.files.items())),
            "edited": sorted(self.edited),
            "timeout_s": self.timeout_s,
            "age_s": round(time.monotonic() - self.created, 1),
        }


@dataclass
class HarnessRun:
    id: str
    task_id: str
    mode: str
    k: int
    seed: int | None
    tag: str
    proposer: str
    status: str = "queued"
    started: float = field(default_factory=time.time)
    finished: float | None = None
    error: str = ""
    summary: dict | None = None
    proposals: dict[str, Proposal] = field(default_factory=dict)
    cancel_requested: bool = False
    done_event: threading.Event = field(default_factory=threading.Event)
    rollout_labels: list[str] = field(default_factory=list)

    @property
    def terminal(self) -> bool:
        return self.status in _TERMINAL

    def public(self, *, include_mission: bool = True) -> dict:
        proposals = []
        for proposal in self.proposals.values():
            row = proposal.public()
            if not include_mission:
                row["mission"] = ""
            proposals.append(row)
        return {
            "id": self.id,
            "task_id": self.task_id,
            "mode": self.mode,
            "k": self.k,
            "seed": self.seed,
            "tag": self.tag,
            "proposer": self.proposer,
            "status": self.status,
            "started": self.started,
            "finished": self.finished,
            "elapsed_s": round((self.finished or time.time()) - self.started, 1),
            "error": self.error,
            # `pending` is True while any propose stage is parked; `proposal` keeps
            # the single-stage shape for simple clients, `proposals` is the full set
            # (INV and DOM propose concurrently, so there are usually two).
            "pending": bool(proposals),
            "proposal": proposals[0] if proposals else None,
            "proposals": proposals,
            "rollouts": list(self.rollout_labels),
            "summary": self.summary,
        }


class HarnessRunner:
    """Owns the single active run and the pending human proposal."""

    def __init__(
        self,
        *,
        emit_start: EmitToolStart | None = None,
        emit_done: EmitToolDone | None = None,
        emit_status: EmitStatus | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._runs: dict[str, HarnessRun] = {}
        self._active: str | None = None
        self.emit_start = emit_start or _noop_start
        self.emit_done = emit_done or _noop_done
        self.emit_status = emit_status or _noop_status

    # -- public surface -------------------------------------------------

    def start(
        self,
        agent: Any,
        *,
        task_id: str,
        mode: str = "agg",
        k: int = 1,
        seed: int | None = None,
        proposer: str = "human",
        proposal_timeout_s: float = 300.0,
    ) -> dict:
        task = str(task_id or "").strip()
        if not task:
            raise ValueError("task_id must not be empty")
        if "/" in task and ".." in task:
            raise ValueError("task_id must not contain '..'")
        if mode not in ("agg", "seq", "both", "parallel", "sequential"):
            raise ValueError("mode must be one of agg, seq, both")
        if int(k) < 1:
            raise ValueError("k must be positive")
        if proposer not in ("human", "dry"):
            raise ValueError("proposer must be 'human' or 'dry'")

        with self._lock:
            if self._active is not None:
                current = self._runs.get(self._active)
                if current is not None and not current.terminal:
                    raise RuntimeError(
                        f"run {current.id} is still {current.status}; "
                        "wait for it, /harness cancel, or /harness status"
                    )
            run_id = "hr-" + uuid.uuid4().hex[:8]
            tag = run_id
            if not _SAFE_TAG.match(tag):  # pragma: no cover - generated value
                tag = "hr"
            run = HarnessRun(
                id=run_id,
                task_id=task,
                mode=mode,
                k=int(k),
                seed=seed,
                tag=tag,
                proposer=proposer,
                status="running",
            )
            self._runs[run_id] = run
            self._active = run_id

        self.emit_status(
            "harness started",
            {"run_id": run.id, "task_id": task, "mode": mode, "k": int(k)},
        )
        thread = threading.Thread(
            target=self._execute,
            args=(run, agent, float(proposal_timeout_s)),
            name=f"harness-{run.id}",
            daemon=True,
        )
        threading.Thread(
            target=self._monitor,
            args=(run, agent),
            name=f"harness-monitor-{run.id}",
            daemon=True,
        ).start()
        thread.start()
        return run.public()

    def list(self) -> list[dict]:
        with self._lock:
            runs = sorted(self._runs.values(), key=lambda r: r.started, reverse=True)
            return [r.public(include_mission=False) for r in runs]

    def get(self, run_id: str) -> dict:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                raise KeyError(run_id)
            return run.public()

    def proposal(self, run_id: str) -> list[dict]:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                raise KeyError(run_id)
            return [p.public() for p in run.proposals.values()]

    def edit(self, run_id: str, path: str, content: str, label: str | None = None) -> dict:
        """Apply one file across every pending proposal that carries it.

        INV and DOM propose concurrently from the same base tree, so a single
        edit normally belongs to both; an explicit label edits just that stage.
        """
        rel = self._clean_path(path)
        with self._lock:
            targets = self._targets(run_id, label=label, path=None if label else rel)
            if not targets:
                raise ValueError(f"no pending proposal carries {rel!r}")
            for proposal in targets:
                proposal.files[rel] = str(content)
                proposal.edited.add(rel)
            return {
                "path": rel,
                "labels": [p.label for p in targets],
                "proposals": [p.public() for p in targets],
            }

    def delete(self, run_id: str, path: str, label: str | None = None) -> dict:
        rel = self._clean_path(path)
        with self._lock:
            targets = self._targets(run_id, label=label, path=None if label else rel)
            if not targets:
                raise ValueError(f"no pending proposal carries {rel!r}")
            for proposal in targets:
                proposal.files.pop(rel, None)
                proposal.edited.add(rel)
            return {"path": rel, "labels": [p.label for p in targets]}

    def respond(self, run_id: str, action: str, label: str | None = None) -> dict:
        if action not in ("accept", "decline"):
            raise ValueError("action must be 'accept' or 'decline'")
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                raise KeyError(run_id)
            targets = self._targets(run_id, label=label)
            if not targets:
                raise ValueError(f"run {run_id} has no pending proposal")
            for proposal in targets:
                proposal.decision = action
                proposal.event.set()
            return {
                "status": "accepted" if action == "accept" else "declined",
                "run_id": run_id,
                "labels": [p.label for p in targets],
            }

    def cancel(self, run_id: str) -> dict:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                raise KeyError(run_id)
            run.cancel_requested = True
            for proposal in run.proposals.values():
                proposal.decision = "decline"
                proposal.event.set()
            if run.terminal:
                return {"status": run.status, "run_id": run_id}
            run.status = "cancelling"
            return {"status": "cancelling", "run_id": run_id}

    # -- engine side ------------------------------------------------------

    def request_proposal(
        self,
        *,
        label: str,
        mission: str,
        files: Mapping[str, str],
        timeout_s: float,
    ) -> dict[str, str] | None:
        """Called on the engine thread; blocks until the web responds."""
        with self._lock:
            run = self._active_run()
            proposal = Proposal(
                label=label,
                mission=str(mission),
                files=dict(files),
                timeout_s=float(timeout_s),
            )
            run.proposals[label] = proposal
            run.status = "waiting"
            run_id = run.id
        tool_id = f"{run_id}:{label}"
        self.emit_start(
            "harness.propose",
            {"run_id": run_id, "label": label, "files": sorted(files)},
            tool_id,
        )
        self.emit_status(
            "harness waiting for your edit",
            {"run_id": run_id, "label": label, "timeout_s": float(timeout_s)},
        )

        got = proposal.event.wait(float(timeout_s))
        with self._lock:
            decision = proposal.decision or ("timeout" if not got else "")
            accepted = dict(proposal.files) if decision == "accept" else None
            run.proposals.pop(label, None)
            if run.cancel_requested:
                run.status = "cancelling"
            elif run.proposals:
                run.status = "waiting"
            else:
                run.status = "running"
        self.emit_done(
            "harness.propose",
            f"{label}: {'accepted' if accepted is not None else decision or 'declined'}",
            tool_id,
            accepted is not None,
        )
        self.emit_status(
            "harness proposal resolved",
            {"run_id": run_id, "label": label, "decision": decision or "decline"},
        )
        return accepted

    # -- internals --------------------------------------------------------

    def _execute(self, run: HarnessRun, agent: Any, proposal_timeout_s: float) -> None:
        try:
            prop = (
                HumanProposer(self, timeout_s=proposal_timeout_s)
                if run.proposer == "human"
                else None
            )
            harness = Harness(agent, runs_root=agent._runs_root())
            result = asyncio.run(
                harness.run_task(
                    run.task_id,
                    mode=run.mode,
                    k=run.k,
                    seed=run.seed,
                    tag=run.tag,
                    prop=prop,
                )
            )
            summary_path = self._summary_path(run, agent)
            run.summary = self._load_summary(summary_path)
            status = str(getattr(result, "status", "") or "ok")
            if run.cancel_requested and status in ("ok", "partial"):
                status = "cancelled"
            run.status = status if status in _TERMINAL else "ok"
            run.error = "; ".join(getattr(result, "errors", []) or [])
        except Exception as exc:  # the run record is the error surface
            run.status = "error"
            run.error = f"{type(exc).__name__}: {exc}"
        finally:
            run.finished = time.time()
            try:
                self._flush_rollouts(run, agent)
            except OSError:
                pass
            run.done_event.set()
            with self._lock:
                if self._active == run.id:
                    self._active = None
                for proposal in run.proposals.values():
                    proposal.decision = "decline"
                    proposal.event.set()
            self.emit_status(
                "harness finished",
                {
                    "run_id": run.id,
                    "status": run.status,
                    "error": run.error,
                    "task_id": run.task_id,
                },
            )

    def _monitor(self, run: HarnessRun, agent: Any) -> None:
        task_dir = self._task_dir(run, agent)
        while not run.done_event.is_set():
            try:
                self._flush_rollouts(run, agent, task_dir)
            except OSError:
                pass
            run.done_event.wait(0.25)

    def _flush_rollouts(
        self, run: HarnessRun, agent: Any, task_dir: Path | None = None
    ) -> None:
        """Emit one pill per rollout dir the engine has produced so far."""
        task_dir = task_dir or self._task_dir(run, agent)
        for label in self._rollout_labels(task_dir):
            with self._lock:
                if label in run.rollout_labels:
                    continue
                run.rollout_labels.append(label)
            tool_id = f"{run.id}:{label}"
            self.emit_start(
                "harness.rollout",
                {"run_id": run.id, "label": label, "task_id": run.task_id},
                tool_id,
            )
            self.emit_done("harness.rollout", f"{label} complete", tool_id, True)

    @staticmethod
    def _clean_path(path: str) -> str:
        clean = str(path or "").replace("\\", "/").strip().lstrip("/")
        parts = [p for p in clean.split("/") if p not in ("", ".")]
        if not parts or ".." in parts:
            raise ValueError("path must stay inside the harness directory")
        return "/".join(parts)

    def _targets(
        self, run_id: str, *, label: str | None = None, path: str | None = None
    ) -> list[Proposal]:
        run = self._runs.get(run_id)
        if run is None:
            raise KeyError(run_id)
        if label is not None:
            proposal = run.proposals.get(label)
            if proposal is None:
                raise ValueError(
                    f"no pending proposal {label!r}; pending: "
                    + (", ".join(sorted(run.proposals)) or "(none)")
                )
            return [proposal]
        if not run.proposals:
            raise ValueError(f"run {run_id} has no pending proposal")
        if path is not None:
            return [p for p in run.proposals.values() if path in p.files]
        return list(run.proposals.values())

    def _active_run(self) -> HarnessRun:
        run = self._runs.get(self._active or "")
        if run is None:
            raise RuntimeError("no active harness run")
        return run

    @staticmethod
    def _task_dir(run: HarnessRun, agent: Any) -> Path:
        spec = getattr(agent, "spec", None)
        substrate = getattr(agent, "substrate", None)
        bench = str(getattr(spec, "name", "april"))
        sub = str(getattr(substrate, "name", "april"))
        persona = str(getattr(spec, "profile", "") or "").strip()
        ns = Namespace(bench, sub, "agg", run.k, run.seed, run.tag, persona)
        return ns.task_dir(Path(agent._runs_root()), run.task_id)

    def _summary_path(self, run: HarnessRun, agent: Any) -> Path:
        return self._task_dir(run, agent) / "summary.json"

    @staticmethod
    def _load_summary(path: Path) -> dict | None:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    @staticmethod
    def _rollout_labels(task_dir: Path) -> list[str]:
        if not task_dir.is_dir():
            return []
        labels: list[str] = []
        for marker in task_dir.rglob(".adapt_complete"):
            labels.append(marker.parent.name)
        for child in task_dir.iterdir():
            if child.is_dir() and re.fullmatch(r"base\d+", child.name):
                if (child / "trajectory.txt").is_file():
                    labels.append(child.name)
        return sorted(set(labels))


__all__ = ["HarnessRunner", "HarnessRun", "Proposal"]
