"""Tests for the MCP runtime client (stdio + HTTP + status reporting)."""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.mcp_client import (
    McpRuntime,
    format_tool_result,
    tool_name_for,
)
from core.mcp_registry import McpRegistry

FAKE_STDIO_SERVER = r'''
import json
import sys

TOOLS = [
    {
        "name": "echo",
        "description": "Echo text",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    }
]

for line in sys.stdin:
    if not line.strip():
        continue
    message = json.loads(line)
    method = message.get("method")
    if "id" not in message:
        continue
    if method == "initialize":
        result = {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "fake", "version": "1"},
        }
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        text = message.get("params", {}).get("arguments", {}).get("text", "")
        result = {"content": [{"type": "text", "text": text}], "isError": False}
    else:
        result = {}
    print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}), flush=True)
'''


def _registry(tmp_path: Path) -> McpRegistry:
    return McpRegistry(tmp_path / "mcp_servers.json")


def _add_stdio(tmp_path: Path, registry: McpRegistry, *, name: str, code: str) -> None:
    script = tmp_path / f"{name.lower()}.py"
    script.write_text(code, encoding="utf-8")
    registry.add(
        name=name,
        transport="stdio",
        command=sys.executable,
        args=[str(script)],
    )


def test_stdio_connect_discover_and_call(tmp_path):
    registry = _registry(tmp_path)
    _add_stdio(tmp_path, registry, name="Fake", code=FAKE_STDIO_SERVER)

    runtime = McpRuntime(registry=registry, connect_timeout=5, call_timeout=5)
    runtime.start()
    try:
        status = runtime.status()
        server = status["servers"][0]
        assert server["name"] == "Fake"
        assert server["status"] == "connected"
        assert [tool["name"] for tool in server["tools"]] == ["echo"]

        schemas = runtime.tool_schemas()
        assert [schema["function"]["name"] for schema in schemas] == [
            "mcp__fake__echo"
        ]
        assert schemas[0]["function"]["parameters"]["required"] == ["text"]

        assert runtime.dispatch("mcp__fake__echo", {"text": "hello"}) == "hello"
        assert runtime.dispatch("not_an_mcp_tool", {}) is None
    finally:
        runtime.close()


def test_failed_server_is_reported_without_blocking_healthy_servers(tmp_path):
    registry = _registry(tmp_path)
    _add_stdio(tmp_path, registry, name="Fake", code=FAKE_STDIO_SERVER)
    registry.add(
        name="Broken",
        transport="stdio",
        command=sys.executable,
        args=["-c", "raise SystemExit(3)"],
    )

    runtime = McpRuntime(registry=registry, connect_timeout=2, call_timeout=2)
    runtime.start()
    try:
        by_name = {
            server["name"]: server for server in runtime.status()["servers"]
        }
        assert by_name["Fake"]["status"] == "connected"
        assert by_name["Broken"]["status"] == "error"
        assert by_name["Broken"]["error"]
        assert "Fake" in [server["name"] for server in runtime.status()["servers"]]

        # A failed server contributes no schemas and cannot be called.
        assert all(
            "broken" not in schema["function"]["name"]
            for schema in runtime.tool_schemas()
        )
    finally:
        runtime.close()


def test_disabled_server_is_listed_but_not_connected(tmp_path):
    registry = _registry(tmp_path)
    registry.add(
        name="Off",
        transport="stdio",
        command=sys.executable,
        enabled=False,
    )

    runtime = McpRuntime(registry=registry, connect_timeout=1)
    runtime.start()
    try:
        server = runtime.status()["servers"][0]
        assert server["status"] == "disabled"
        assert server["tools"] == []
        assert runtime.tool_schemas() == []
    finally:
        runtime.close()


class _HttpMcpHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _reply(self, message, *, sse=False, session_id=False):
        payload = json.dumps(message)
        if sse:
            body = f"event: message\ndata: {payload}\n\n".encode()
            content_type = "text/event-stream"
        else:
            body = payload.encode()
            content_type = "application/json"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if session_id:
            self.send_header("Mcp-Session-Id", "session-1")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler API
        length = int(self.headers.get("Content-Length", 0))
        message = json.loads(self.rfile.read(length))
        method = message.get("method")
        if "id" not in message:
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if method == "initialize":
            result = {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "http-fake", "version": "1"},
            }
            self._reply(
                {"jsonrpc": "2.0", "id": message["id"], "result": result},
                session_id=True,
            )
        elif method == "tools/list":
            result = {
                "tools": [
                    {
                        "name": "echo",
                        "description": "Echo text",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"text": {"type": "string"}},
                        },
                    }
                ]
            }
            # Exercise the SSE branch of the HTTP transport.
            self._reply(
                {"jsonrpc": "2.0", "id": message["id"], "result": result},
                sse=True,
            )
        elif method == "tools/call":
            text = message.get("params", {}).get("arguments", {}).get("text", "")
            self._reply(
                {
                    "jsonrpc": "2.0",
                    "id": message["id"],
                    "result": {"content": [{"type": "text", "text": text}]},
                }
            )
        else:
            self._reply({"jsonrpc": "2.0", "id": message["id"], "result": {}})

    def do_DELETE(self):  # noqa: N802 - BaseHTTPRequestHandler API
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):  # keep test output clean
        pass


def test_http_connect_discover_and_call(tmp_path):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _HttpMcpHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        registry = _registry(tmp_path)
        registry.add(
            name="Http Fake",
            transport="http",
            url=f"http://127.0.0.1:{server.server_address[1]}/mcp",
        )
        runtime = McpRuntime(registry=registry, connect_timeout=5, call_timeout=5)
        runtime.start()
        try:
            status = runtime.status()["servers"][0]
            assert status["status"] == "connected"
            assert status["tools"][0]["name"] == "echo"
            assert runtime.dispatch("mcp__http-fake__echo", {"text": "over http"}) == (
                "over http"
            )
        finally:
            runtime.close()
    finally:
        server.shutdown()
        server.server_close()


def test_tool_name_is_namespaced_and_bounded():
    assert tool_name_for("fake", "echo") == "mcp__fake__echo"
    long_name = tool_name_for("server", "t" * 100)
    assert len(long_name) <= 64
    assert long_name.startswith("mcp__server__")


def test_format_tool_result_handles_error_and_non_text_content():
    assert (
        format_tool_result({"content": [{"type": "text", "text": "ok"}]}) == "ok"
    )
    assert format_tool_result({"content": [], "isError": True}).startswith("Error:")
    formatted = format_tool_result(
        {"content": [{"type": "image", "mimeType": "image/png", "data": "..."}]}
    )
    assert "image/png" in formatted
