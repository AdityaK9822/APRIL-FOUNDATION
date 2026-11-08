"""Tests for the subagent feature adapter (depth guard, run, list)."""

from __future__ import annotations

import json
from types import SimpleNamespace

from core.feature_adapter import SubagentFeatureAdapter


class FakeManager:
    def __init__(self):
        self.spawned = []
        self.mode = "ok"
        self.last_call = None
        self.shutdown_called = False

    def spawn(self, config):
        process = SimpleNamespace(id=f"sub-{len(self.spawned) + 1}", config=config)
        self.spawned.append(process)
        return process

    def dispatch_and_wait(self, subagent_id, task, timeout=None):
        self.last_call = (subagent_id, task, timeout)
        if self.mode == "timeout":
            raise TimeoutError()
        if self.mode == "error":
            raise RuntimeError("dispatch exploded")
        return SimpleNamespace(
            success=True,
            data=f"done: {task.description}",
            error=None,
        )

    def list_subagents(self):
        return [{"id": "sub-1", "status": "completed"}]

    def shutdown(self, wait=False):
        self.shutdown_called = True


def _agent(depth: int = 0) -> SimpleNamespace:
    return SimpleNamespace(config=SimpleNamespace(_subagent_depth=depth))


def test_depth_guard_removes_subagent_tools():
    parent = _agent(depth=0)
    child = _agent(depth=1)
    adapter = SubagentFeatureAdapter(manager_factory=lambda agent: FakeManager())

    parent_names = [schema["function"]["name"] for schema in adapter.tool_schemas(parent)]
    assert parent_names == ["run_subagent", "list_subagents"]
    assert "run_subagent" in adapter.pre_call(parent, "go")

    assert adapter.tool_schemas(child) == []
    assert adapter.pre_call(child, "go") == ""
    assert adapter.dispatch("run_subagent", {"description": "x"}, child) is None


def test_run_subagent_spawns_and_returns_result():
    manager = FakeManager()
    adapter = SubagentFeatureAdapter(manager_factory=lambda agent: manager)
    agent = _agent()

    result = adapter.dispatch(
        "run_subagent",
        {"description": "inspect the tree", "role": "analyst", "timeout": 12},
        agent,
    )

    assert result == "done: inspect the tree"
    assert len(manager.spawned) == 1
    spawned = manager.spawned[0]
    assert spawned.config.role == "analyst"
    assert spawned.config.max_iterations == 50
    subagent_id, task, timeout = manager.last_call
    assert subagent_id == spawned.id
    assert task.description == "inspect the tree"
    assert timeout == 12.0


def test_run_subagent_timeout_and_dispatch_error_are_reported():
    manager = FakeManager()
    adapter = SubagentFeatureAdapter(manager_factory=lambda agent: manager)
    agent = _agent()

    manager.mode = "timeout"
    timed_out = adapter.dispatch("run_subagent", {"description": "slow"}, agent)
    assert "did not finish" in timed_out
    assert "sub-1" in timed_out

    manager.mode = "error"
    failed = adapter.dispatch("run_subagent", {"description": "broken"}, agent)
    assert "Error running subagent: dispatch exploded" in failed

    empty = adapter.dispatch("run_subagent", {"description": "  "}, agent)
    assert "description is required" in empty


def test_list_subagents_and_close():
    manager = FakeManager()
    adapter = SubagentFeatureAdapter(manager_factory=lambda agent: manager)
    agent = _agent()

    assert "No subagents" in adapter.dispatch("list_subagents", {}, agent)

    adapter.dispatch("run_subagent", {"description": "work"}, agent)
    listing = json.loads(adapter.dispatch("list_subagents", {}, agent))
    assert listing[0]["status"] == "completed"

    # Depth-guarded adapters must not claim the tool.
    assert adapter.dispatch("list_subagents", {}, _agent(depth=1)) is None

    adapter.close()
    assert manager.shutdown_called is True
    assert adapter._manager is None
