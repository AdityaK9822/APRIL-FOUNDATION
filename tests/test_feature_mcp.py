"""Tests for the MCP feature adapter (context, schemas, dispatch, status)."""

from __future__ import annotations

from types import SimpleNamespace

from core.feature_adapter import McpFeatureAdapter


class FakeRuntime:
    def __init__(self):
        self.starts = 0
        self.refreshes = 0
        self.closed = False
        self.calls = []
        self.schemas = [
            {
                "type": "function",
                "function": {
                    "name": "mcp__fake__echo",
                    "description": "Echo text",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]
        self.status_value = {
            "error": "",
            "servers": [
                {
                    "id": "fake",
                    "name": "Fake",
                    "transport": "stdio",
                    "enabled": True,
                    "status": "connected",
                    "error": "",
                    "protocol_version": "2024-11-05",
                    "tools": [
                        {
                            "name": "echo",
                            "exposed_name": "mcp__fake__echo",
                            "description": "Echo text",
                            "status": "available",
                            "error": "",
                        }
                    ],
                }
            ],
            "tools": [],
        }

    def start(self):
        self.starts += 1

    def refresh(self):
        self.refreshes += 1

    def close(self):
        self.closed = True

    def tool_schemas(self):
        return self.schemas

    def dispatch(self, tool_name, arguments):
        self.calls.append((tool_name, arguments))
        if tool_name == "mcp__fake__echo":
            return f"echo: {arguments.get('text', '')}"
        return None

    def status(self):
        return self.status_value


def _agent(depth: int = 0) -> SimpleNamespace:
    return SimpleNamespace(config=SimpleNamespace(_subagent_depth=depth))


def test_mcp_adapter_exposes_context_schemas_and_dispatch():
    runtime = FakeRuntime()
    adapter = McpFeatureAdapter(runtime=runtime)
    agent = _agent()

    context = adapter.pre_call(agent, "hello")
    assert "MCP servers:" in context
    assert "Fake: connected (1 tools)" in context

    schemas = adapter.tool_schemas(agent)
    assert schemas[0]["function"]["name"] == "mcp__fake__echo"

    assert adapter.dispatch("mcp__fake__echo", {"text": "hi"}, agent) == "echo: hi"
    assert adapter.dispatch("not_mcp", {}, agent) is None
    assert runtime.calls == [
        ("mcp__fake__echo", {"text": "hi"}),
        ("not_mcp", {}),
    ]

    status = adapter.status(agent)
    assert status["servers"][0]["status"] == "connected"


def test_mcp_adapter_is_disabled_for_subagent_runtimes():
    runtime = FakeRuntime()
    adapter = McpFeatureAdapter(runtime=runtime)
    child = _agent(depth=1)

    assert adapter.pre_call(child, "hello") == ""
    assert adapter.tool_schemas(child) == []
    assert adapter.dispatch("mcp__fake__echo", {"text": "hi"}, child) is None
    assert runtime.starts == 0
    assert runtime.calls == []

    status = adapter.status(child)
    assert status["servers"] == []
    assert "disabled" in status["error"].lower()


def test_mcp_adapter_reports_registry_error_in_context():
    runtime = FakeRuntime()
    runtime.status_value = {"error": "registry unreadable", "servers": [], "tools": []}
    adapter = McpFeatureAdapter(runtime=runtime)

    assert adapter.pre_call(_agent(), "hello") == "MCP: registry unreadable"


def test_mcp_adapter_refresh_and_close_delegate():
    runtime = FakeRuntime()
    adapter = McpFeatureAdapter(runtime=runtime)
    agent = _agent()

    adapter.start(agent)
    adapter.start(agent)  # idempotent
    assert runtime.starts == 1

    adapter.refresh(agent)
    assert runtime.refreshes == 1

    adapter.close()
    assert runtime.closed is True

    # close is terminal; a later request must not silently reconnect.
    adapter.start(agent)
    assert runtime.starts == 1
    adapter.refresh(agent)
    assert runtime.refreshes == 2
