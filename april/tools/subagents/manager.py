"""Background subagent tasks with a cooperative cancel."""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass

from .profile import SubagentProfile
from .runner import SubagentRunner


@dataclass
class TaskState:
    id: str
    profile: str
    task: str
    status: str = "running"
    result: str = ""
    error: str = ""


class SubagentManager:
    def __init__(self, runner: SubagentRunner):
        self.runner = runner
        self._tasks: dict[str, TaskState] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._cancels: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def run(self, profile: SubagentProfile, task: str) -> str:
        return self.runner.run(profile, task)

    def start(self, profile: SubagentProfile, task: str) -> str:
        task_id = uuid.uuid4().hex[:8]
        state = TaskState(id=task_id, profile=profile.name, task=task)
        cancel = threading.Event()
        with self._lock:
            self._tasks[task_id] = state
            self._cancels[task_id] = cancel
        thread = threading.Thread(
            target=self._run,
            args=(state, profile, task, cancel),
            daemon=True,
        )
        self._threads[task_id] = thread
        thread.start()
        return task_id

    def _run(
        self,
        state: TaskState,
        profile: SubagentProfile,
        task: str,
        cancel: threading.Event,
    ) -> None:
        try:
            state.result = self.runner.run(profile, task, cancel=cancel)
            state.status = "cancelled" if cancel.is_set() else "done"
        except Exception as exc:
            state.status = "error"
            state.error = str(exc)

    def wait(self, task_id: str, timeout: float | None = None) -> TaskState | None:
        thread = self._threads.get(task_id)
        if thread is not None:
            thread.join(timeout)
        return self.status(task_id)

    def stop(self, task_id: str) -> bool:
        cancel = self._cancels.get(task_id)
        if cancel is None:
            return False
        cancel.set()
        return True

    def status(self, task_id: str) -> TaskState | None:
        return self._tasks.get(task_id)

    def tasks(self) -> list[TaskState]:
        return list(self._tasks.values())
