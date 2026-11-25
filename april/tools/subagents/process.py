"""Process-isolated subagent runner."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from .profile import SubagentProfile


class ProcessSubagentRunner:
    def __init__(self, workspace: str | Path | None = None, timeout: float = 600.0):
        self.workspace = str(Path(workspace or ".").resolve())
        self.timeout = timeout
        self.repo_root = str(Path(__file__).resolve().parents[3])

    def run(self, profile: SubagentProfile, task: str) -> str:
        payload = {
            "profile": profile.to_dict(),
            "task": task,
            "workspace": self.workspace,
        }
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            part for part in (self.repo_root, env.get("PYTHONPATH", "")) if part
        )
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "april.tools.subagents.worker"],
                input=json.dumps(payload),
                text=True,
                capture_output=True,
                timeout=self.timeout,
                cwd=self.workspace,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"subagent worker timed out after {self.timeout}s") from exc
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()[:500]
            raise RuntimeError(f"subagent worker failed (exit {proc.returncode}): {detail}")
        try:
            result = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"subagent worker emitted invalid JSON: {proc.stdout[:500]}"
            ) from exc
        if not result.get("ok"):
            raise RuntimeError(str(result.get("error") or "subagent worker failed"))
        return str(result.get("output") or "")
