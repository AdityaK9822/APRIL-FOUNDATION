"""Run matching hooks; exit code 2 or a JSON ``block`` decision stops the tool."""

from __future__ import annotations

import json
import re
import subprocess
import threading

from ..base import HookDecision, HookEvent
from .schema import HookSet, HookSpec


class HookRunner:
    def __init__(self, hook_set: HookSet, max_output: int = 32768):
        self.hook_set = hook_set
        self.max_output = max_output
        self.errors: list[str] = []
        self._error_lock = threading.Lock()

    def fire(self, event: HookEvent | str, payload: dict | None = None) -> HookDecision:
        name = event.value if isinstance(event, HookEvent) else str(event)
        payload = dict(payload or {})
        matching = [
            hook
            for hook in self.hook_set.hooks
            if hook.event == name and _matches(hook.matcher, payload)
        ]
        contexts: list[str] = []
        for hook in matching:
            if hook.async_:
                threading.Thread(
                    target=self._run_async,
                    args=(hook, name, payload),
                    daemon=True,
                ).start()
                continue
            decision = self._run(hook, name, payload)
            if not decision.allow:
                return decision
            if decision.context:
                contexts.append(decision.context)
        return HookDecision(True, "", "\n".join(contexts))

    def _run_async(self, hook: HookSpec, event: str, payload: dict) -> None:
        try:
            decision = self._run(hook, event, payload)
            if not decision.allow:
                with self._error_lock:
                    self.errors.append(f"{hook.command}: {decision.reason}")
        except Exception as exc:
            with self._error_lock:
                self.errors.append(f"{hook.command}: {exc}")

    def _run(self, hook: HookSpec, event: str, payload: dict) -> HookDecision:
        stdin = json.dumps({"hook_event_name": event, **payload}, ensure_ascii=False)
        try:
            if hook.kind == "process":
                proc = subprocess.run(
                    [hook.command, *hook.args],
                    input=stdin,
                    text=True,
                    capture_output=True,
                    timeout=hook.timeout,
                )
            else:
                proc = subprocess.run(
                    hook.command,
                    shell=True,
                    input=stdin,
                    text=True,
                    capture_output=True,
                    timeout=hook.timeout,
                )
        except Exception as exc:
            if hook.on_error == "deny":
                return HookDecision(False, f"{hook.command}: {exc}")
            return HookDecision(True)

        if proc.returncode == 2:
            return HookDecision(False, (proc.stderr or "hook blocked").strip())
        if proc.returncode != 0 and hook.on_error == "deny":
            return HookDecision(False, (proc.stderr or f"exit {proc.returncode}").strip())

        output = (proc.stdout or "").strip()
        if not output:
            return HookDecision(True)
        try:
            data = json.loads(output)
        except json.JSONDecodeError:
            return HookDecision(True, "", output[: self.max_output])
        if not isinstance(data, dict):
            return HookDecision(True, "", output[: self.max_output])
        if data.get("decision") == "block" or data.get("continue") is False:
            reason = data.get("reason") or data.get("stopReason") or "hook blocked"
            return HookDecision(False, str(reason))
        return HookDecision(
            True,
            str(data.get("systemMessage") or ""),
            str(data.get("additionalContext") or "")[: self.max_output],
        )


def _matches(matcher: str, payload: dict) -> bool:
    if not matcher:
        return True
    tool_name = str(payload.get("tool_name") or "")
    try:
        return re.fullmatch(matcher, tool_name) is not None
    except re.error:
        return matcher == tool_name
