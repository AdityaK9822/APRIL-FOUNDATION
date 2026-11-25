from pathlib import Path

from fastapi.testclient import TestClient

from april.api.app import create_app
from april.body import FakeDevices
from april.events import EventBus
from april.llm import ChatReply, FakeModel
from april.memory import InMemoryMemoryStore
from april.memory.patterns import PatternStore
from april.persona import Persona
from april.runtime import Runtime
from april.tools import BuiltinSource, McpRegistry, Toolset


def _runtime(tmp_path: Path) -> Runtime:
    return Runtime(
        FakeModel([ChatReply(text="hello")]),
        "test",
        InMemoryMemoryStore(),
        Persona(
            name="test",
            system_text="You are a test.",
            guardrails="",
            temperature=0.2,
            source="test",
        ),
        Toolset([BuiltinSource(workspace=tmp_path)]),
        EventBus(),
        FakeDevices(),
    )


def _client(tmp_path: Path) -> TestClient:
    app = create_app(
        _runtime(tmp_path),
        registry=McpRegistry(tmp_path / "mcp.json"),
        patterns=PatternStore(tmp_path / "patterns.db"),
    )
    return TestClient(app)


def test_health_and_chat(tmp_path):
    client = _client(tmp_path)
    assert client.get("/healthz").json()["status"] == "ok"
    response = client.post("/api/chat", json={"message": "hi"})
    assert "chat.done" in response.text
    assert "hello" in response.text
    history = client.get("/api/events/history").json()
    assert history["count"] >= 1
    current = client.get("/api/session/current").json()
    assert current["session_id"] == "default"
    assert len(current["messages"]) == 2


def test_sessions_and_retired_routes(tmp_path):
    client = _client(tmp_path)
    created = client.post("/api/sessions/new").json()
    assert created["session_id"].startswith("session-")
    current = client.get("/api/session/current").json()
    assert current["session_id"] == created["session_id"]
    assert client.get("/api/sessions").json()["available"] is True
    assert isinstance(client.get("/api/skills").json()["skills"], list)
    assert isinstance(client.get("/api/tools").json()["tools"], list)
    assert isinstance(client.get("/api/mcp/servers").json()["servers"], list)
    assert client.get("/api/experience").status_code == 410
    assert client.get("/api/ade/anything").status_code == 410
    assert client.get("/api/harness/anything").status_code == 410
    assert client.get("/api/patterns").status_code == 200


def test_mcp_crud(tmp_path):
    client = _client(tmp_path)
    created = client.post(
        "/api/mcp/servers",
        json={"name": "Local", "transport": "stdio", "command": "echo"},
    )
    assert created.status_code == 201
    assert created.json()["id"] == "local"
    assert client.get("/api/mcp/servers").json()["servers"][0]["name"] == "Local"
    assert client.delete("/api/mcp/servers/local").status_code == 200
