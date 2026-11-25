"""Minimal MCP (Model Context Protocol) runtime client.

Implements the subset APRIL needs to expose MCP servers as LLM tools:

    registry record -> connect -> initialize -> tools/list -> tools/call

Transports:
- **stdio**: spawn the server command and speak newline-delimited JSON-RPC on
  stdin/stdout (stderr is captured for diagnostics).
- **http**: MCP Streamable HTTP — POST JSON-RPC, accept JSON or SSE responses,
  keep the ``Mcp-Session-Id`` returned by ``initialize``.

No third-party dependencies. Connections are owned by :class:`McpRuntime`,
which is started eagerly by the MCP feature adapter and closed with the agent.
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from typing import Any, Dict, List, Optional

from .oauth import OAuthError, TokenProvider
from .registry import McpRegistry, slugify

PROTOCOL_VERSION = "2024-11-05"
CLIENT_INFO = {"name": "april-agent", "version": "0.1.0"}
DEFAULT_CONNECT_TIMEOUT = 10.0
DEFAULT_CALL_TIMEOUT = 60.0
MAX_TOOL_NAME = 64

_NAME_RX = re.compile(r"[^A-Za-z0-9_-]+")


class McpError(Exception):
    """A connection/protocol failure; str(e) is safe to show in the UI."""


# ---------------------------------------------------------------------------
# JSON-RPC connections
# ---------------------------------------------------------------------------


class _Connection:
    """One JSON-RPC connection to one MCP server."""

    def __init__(self, record: Dict[str, Any]) -> None:
        self.record = dict(record or {})
        self.server_id = str(
            self.record.get("id") or slugify(self.record.get("name") or "")
        )
        self.name = str(self.record.get("name") or self.server_id)
        self.transport = str(self.record.get("transport") or "")
        self.protocol_version = ""
        self.tools: List[Dict[str, Any]] = []
        self._next_id = 0
        self._request_lock = threading.RLock()

    # -- transport hooks -----------------------------------------------------

    def _send(
        self, message: Dict[str, Any], timeout: Optional[float] = None
    ) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def _recv(self, timeout: float) -> Dict[str, Any]:  # pragma: no cover - abstract
        raise NotImplementedError

    def close(self) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    # -- protocol ------------------------------------------------------------

    def request(
        self,
        method: str,
        params: Optional[Dict[str, Any]] = None,
        timeout: float = DEFAULT_CALL_TIMEOUT,
    ) -> Dict[str, Any]:
        """Send one request and return its ``result`` (or raise ``McpError``)."""
        with self._request_lock:
            self._next_id += 1
            request_id = self._next_id
            message: Dict[str, Any] = {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
            }
            if params is not None:
                message["params"] = params
            self._send(message, timeout)

            deadline = time.monotonic() + max(0.001, timeout)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise McpError(f"{self.name}: timed out waiting for {method}")
                response = self._recv(remaining)
                if not isinstance(response, dict):
                    continue
                if response.get("id") != request_id:
                    # Notifications and stale responses are not ours.
                    continue
                error = response.get("error")
                if error:
                    if isinstance(error, dict):
                        detail = error.get("message") or json.dumps(error)
                    else:
                        detail = str(error)
                    raise McpError(f"{self.name}: {method} failed: {detail}")
                result = response.get("result")
                return result if isinstance(result, dict) else {"value": result}

    def notify(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        message: Dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        self._send(message)

    def initialize(self, timeout: float = DEFAULT_CONNECT_TIMEOUT) -> Dict[str, Any]:
        result = self.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": CLIENT_INFO,
            },
            timeout=timeout,
        )
        self.protocol_version = str(result.get("protocolVersion") or "")
        self.notify("notifications/initialized")
        return result

    def list_tools(self, timeout: float = DEFAULT_CONNECT_TIMEOUT) -> List[Dict[str, Any]]:
        tools: List[Dict[str, Any]] = []
        cursor: Optional[str] = None
        while True:
            params = {"cursor": cursor} if cursor else {}
            result = self.request("tools/list", params, timeout=timeout)
            batch = result.get("tools")
            if isinstance(batch, list):
                tools.extend(item for item in batch if isinstance(item, dict))
            cursor = result.get("nextCursor")
            if not cursor:
                break
        self.tools = tools
        return tools

    def call_tool(
        self,
        name: str,
        arguments: Dict[str, Any],
        timeout: float = DEFAULT_CALL_TIMEOUT,
    ) -> Dict[str, Any]:
        return self.request(
            "tools/call",
            {"name": name, "arguments": dict(arguments or {})},
            timeout=timeout,
        )


class StdioConnection(_Connection):
    """Newline-delimited JSON-RPC over a child process's stdio."""

    def __init__(self, record: Dict[str, Any]) -> None:
        super().__init__(record)
        command = str(self.record.get("command") or "").strip()
        if not command:
            raise McpError(f"{self.name}: stdio transport requires a command")
        args = [str(arg) for arg in (self.record.get("args") or [])]
        env = os.environ.copy()
        env.update(
            {
                str(key): str(value)
                for key, value in (self.record.get("env") or {}).items()
            }
        )
        try:
            self._process = subprocess.Popen(
                [command, *args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            raise McpError(f"{self.name}: cannot start {command!r}: {exc}") from exc

        self._inbox: "queue.Queue[Optional[Dict[str, Any]]]" = queue.Queue()
        self._stderr: deque[str] = deque(maxlen=40)
        self._reader = threading.Thread(
            target=self._read_stdout, name=f"mcp-{self.server_id}-stdout", daemon=True
        )
        self._stderr_reader = threading.Thread(
            target=self._read_stderr, name=f"mcp-{self.server_id}-stderr", daemon=True
        )
        self._reader.start()
        self._stderr_reader.start()

    def _read_stdout(self) -> None:
        assert self._process.stdout is not None
        try:
            for line in self._process.stdout:
                payload = line.strip()
                if not payload:
                    continue
                try:
                    self._inbox.put(json.loads(payload))
                except json.JSONDecodeError:
                    self._stderr.append(f"non-JSON stdout: {payload[:300]}")
        finally:
            self._inbox.put(None)

    def _read_stderr(self) -> None:
        assert self._process.stderr is not None
        for line in self._process.stderr:
            self._stderr.append(line.rstrip())

    def stderr_tail(self) -> str:
        return " | ".join(line for line in list(self._stderr)[-5:] if line)

    def _send(
        self, message: Dict[str, Any], timeout: Optional[float] = None
    ) -> None:
        process = self._process
        if process.poll() is not None:
            tail = self.stderr_tail()
            detail = f": {tail}" if tail else ""
            raise McpError(
                f"{self.name}: server exited with code {process.returncode}{detail}"
            )
        assert process.stdin is not None
        try:
            process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
            process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise McpError(f"{self.name}: write failed: {exc}") from exc

    def _recv(self, timeout: float) -> Dict[str, Any]:
        try:
            item = self._inbox.get(timeout=max(0.001, timeout))
        except queue.Empty as exc:
            raise McpError(f"{self.name}: timed out waiting for a response") from exc
        if item is None:
            tail = self.stderr_tail()
            detail = f": {tail}" if tail else ""
            raise McpError(f"{self.name}: connection closed{detail}")
        return item

    def close(self) -> None:
        process = getattr(self, "_process", None)
        if process is None:
            return
        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
        except OSError:
            pass


class HttpConnection(_Connection):
    """MCP Streamable HTTP: POST JSON-RPC, JSON or SSE responses."""

    def __init__(self, record: Dict[str, Any]) -> None:
        super().__init__(record)
        self.url = str(self.record.get("url") or "").strip()
        if not self.url:
            raise McpError(f"{self.name}: http transport requires a url")
        self.session_id = ""
        self._inbox: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        self._closed = False
        self._tokens = TokenProvider()

    def _headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        try:
            token = self._tokens.token(self.record)
        except OAuthError as exc:
            raise McpError(f"{self.name}: OAuth failed: {exc}") from exc
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _send(
        self, message: Dict[str, Any], timeout: Optional[float] = None
    ) -> None:
        if self._closed:
            raise McpError(f"{self.name}: connection closed")
        body = json.dumps(message).encode("utf-8")
        request = urllib.request.Request(
            self.url, data=body, headers=self._headers(), method="POST"
        )
        try:
            with urllib.request.urlopen(
                request, timeout=timeout or DEFAULT_CONNECT_TIMEOUT
            ) as response:
                session_id = response.headers.get("Mcp-Session-Id")
                if session_id:
                    self.session_id = session_id
                content_type = response.headers.get("Content-Type", "")
                raw = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:300]
            except OSError:
                pass
            raise McpError(
                f"{self.name}: HTTP {exc.code}{': ' + detail if detail else ''}"
            ) from exc
        except (urllib.error.URLError, OSError) as exc:
            raise McpError(f"{self.name}: HTTP request failed: {exc}") from exc

        for payload in _decode_response(raw, content_type):
            self._inbox.put(payload)

    def _recv(self, timeout: float) -> Dict[str, Any]:
        try:
            return self._inbox.get(timeout=max(0.001, timeout))
        except queue.Empty as exc:
            raise McpError(f"{self.name}: timed out waiting for a response") from exc

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if not self.session_id:
            return
        request = urllib.request.Request(
            self.url,
            headers={"Mcp-Session-Id": self.session_id},
            method="DELETE",
        )
        try:
            with urllib.request.urlopen(request, timeout=2):
                pass
        except (urllib.error.URLError, OSError):
            pass


def _decode_response(raw: str, content_type: str) -> List[Dict[str, Any]]:
    """Decode a JSON or SSE HTTP response into JSON-RPC messages."""
    if not raw.strip():
        return []
    if "text/event-stream" in content_type.lower() or raw.lstrip().startswith(
        ("event:", "data:")
    ):
        messages: List[Dict[str, Any]] = []
        data_lines: List[str] = []
        for line in raw.splitlines():
            if not line.strip():
                if data_lines:
                    messages.extend(_json_messages("\n".join(data_lines)))
                    data_lines = []
                continue
            if line.startswith(":"):
                continue
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
        if data_lines:
            messages.extend(_json_messages("\n".join(data_lines)))
        return messages
    return _json_messages(raw)


def _json_messages(payload: str) -> List[Dict[str, Any]]:
    try:
        value = json.loads(payload)
    except json.JSONDecodeError:
        return []
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _connection_for(record: Dict[str, Any]) -> _Connection:
    transport = str(record.get("transport") or "")
    if transport == "stdio":
        return StdioConnection(record)
    if transport == "http":
        return HttpConnection(record)
    raise McpError(f"unsupported MCP transport: {transport!r}")


# ---------------------------------------------------------------------------
# Registry -> runtime
# ---------------------------------------------------------------------------


def tool_name_for(server_id: str, tool_name: str) -> str:
    """OpenAI-safe namespaced tool name, stable and collision-resistant."""
    raw = f"mcp__{server_id}__{tool_name}"
    cleaned = _NAME_RX.sub("_", raw)
    if len(cleaned) > MAX_TOOL_NAME:
        digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]
        cleaned = f"{cleaned[: MAX_TOOL_NAME - 9]}_{digest}"
    return cleaned


def format_tool_result(result: Dict[str, Any]) -> str:
    """Flatten an MCP ``tools/call`` result into text for the LLM."""
    content = result.get("content")
    parts: List[str] = []
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                parts.append(str(item))
                continue
            kind = item.get("type")
            if kind == "text":
                parts.append(str(item.get("text") or ""))
            else:
                parts.append(json.dumps(item, ensure_ascii=False))
    elif content is not None:
        parts.append(str(content))
    text = "\n".join(part for part in parts if part).strip()
    if result.get("isError"):
        return f"Error: {text or 'MCP tool reported an error'}"
    return text or "(MCP tool returned no content)"


class McpRuntime:
    """Connects enabled registry servers, discovers their tools, routes calls."""

    def __init__(
        self,
        registry: Optional[McpRegistry] = None,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        call_timeout: float = DEFAULT_CALL_TIMEOUT,
    ) -> None:
        self.registry = registry or McpRegistry()
        self.connect_timeout = connect_timeout
        self.call_timeout = call_timeout
        self._lock = threading.RLock()
        self._started = False
        self._connections: Dict[str, _Connection] = {}
        self._servers: Dict[str, Dict[str, Any]] = {}
        self._tools: Dict[str, Dict[str, Any]] = {}
        self._error = ""

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
            self._connect_all()

    def refresh(self) -> None:
        """Close every connection and reconnect from the current registry."""
        with self._lock:
            self._close_locked()
            self._started = True
            self._connect_all()

    def close(self) -> None:
        with self._lock:
            self._close_locked()
            self._started = False

    def _close_locked(self) -> None:
        for connection in list(self._connections.values()):
            try:
                connection.close()
            except Exception:
                pass
        self._connections.clear()
        self._servers.clear()
        self._tools.clear()
        self._error = ""

    def _connect_all(self) -> None:
        try:
            records = self.registry.list()
        except Exception as exc:
            self._error = f"MCP registry unreadable: {exc}"
            return

        for record in records:
            server_id = str(
                record.get("id") or slugify(record.get("name") or "server")
            )
            if not record.get("enabled", True):
                self._servers[server_id] = _server_state(
                    record, status="disabled", error=""
                )
                continue
            self._connect(record)

    def _connect(self, record: Dict[str, Any]) -> None:
        server_id = str(record.get("id") or slugify(record.get("name") or "server"))
        connection: Optional[_Connection] = None
        try:
            connection = _connection_for(record)
            connection.initialize(self.connect_timeout)
            raw_tools = connection.list_tools(self.connect_timeout)
            state = _server_state(
                record,
                status="connected",
                error="",
                protocol_version=connection.protocol_version,
            )
            for raw_tool in raw_tools:
                name = str(raw_tool.get("name") or "")
                if not name:
                    continue
                exposed = tool_name_for(server_id, name)
                tool = {
                    "server_id": server_id,
                    "server_name": str(record.get("name") or server_id),
                    "name": name,
                    "exposed_name": exposed,
                    "description": str(raw_tool.get("description") or ""),
                    "input_schema": _input_schema(raw_tool),
                    "status": "available",
                    "error": "",
                }
                state["tools"].append(
                    {
                        "name": name,
                        "exposed_name": exposed,
                        "description": tool["description"],
                        "status": "available",
                        "error": "",
                    }
                )
                self._tools[exposed] = tool
            self._connections[server_id] = connection
            self._servers[server_id] = state
        except Exception as exc:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass
            self._servers[server_id] = _server_state(
                record, status="error", error=str(exc)
            )

    # -- LLM surface ---------------------------------------------------------

    def tool_schemas(self) -> List[Dict[str, Any]]:
        with self._lock:
            schemas: List[Dict[str, Any]] = []
            for tool in self._tools.values():
                if tool.get("status") != "available":
                    continue
                schemas.append(
                    {
                        "type": "function",
                        "function": {
                            "name": tool["exposed_name"],
                            "description": tool["description"]
                            or f"MCP tool {tool['name']} from {tool['server_name']}",
                            "parameters": tool["input_schema"],
                        },
                    }
                )
            return schemas

    def dispatch(self, exposed_name: str, arguments: Dict[str, Any]) -> Optional[str]:
        """Run a namespaced MCP tool, or return None when the name is not ours."""
        with self._lock:
            tool = self._tools.get(exposed_name)
            if tool is None:
                return None
            connection = self._connections.get(tool["server_id"])
            if connection is None:
                state = self._servers.get(tool["server_id"], {})
                return (
                    f"Error: MCP server '{tool['server_name']}' is not connected"
                    + (f": {state.get('error')}" if state.get("error") else "")
                )
            try:
                result = connection.call_tool(
                    tool["name"], dict(arguments or {}), self.call_timeout
                )
            except Exception as exc:
                tool["status"] = "error"
                tool["error"] = str(exc)
                for entry in self._servers.get(tool["server_id"], {}).get("tools", []):
                    if entry.get("exposed_name") == exposed_name:
                        entry["status"] = "error"
                        entry["error"] = str(exc)
                return f"Error: MCP tool '{tool['name']}' failed: {exc}"
            return format_tool_result(result)

    # -- UI surface ----------------------------------------------------------

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "error": self._error,
                "servers": [
                    dict(state, tools=[dict(tool) for tool in state.get("tools", [])])
                    for state in self._servers.values()
                ],
                "tools": [dict(tool) for tool in self._tools.values()],
            }


def _server_state(
    record: Dict[str, Any],
    *,
    status: str,
    error: str,
    protocol_version: str = "",
) -> Dict[str, Any]:
    server_id = str(record.get("id") or slugify(record.get("name") or "server"))
    return {
        "id": server_id,
        "name": str(record.get("name") or server_id),
        "transport": str(record.get("transport") or ""),
        "enabled": bool(record.get("enabled", True)),
        "status": status,
        "error": error,
        "protocol_version": protocol_version,
        "tools": [],
    }


def _input_schema(raw_tool: Dict[str, Any]) -> Dict[str, Any]:
    schema = raw_tool.get("inputSchema") or raw_tool.get("input_schema")
    if isinstance(schema, dict):
        # MCP servers must return an object schema; default the common bits.
        schema = dict(schema)
        schema.setdefault("type", "object")
        schema.setdefault("properties", {})
        return schema
    return {"type": "object", "properties": {}}


# ---------------------------------------------------------------------------
# Optional probe used by the registry "Test" button
# ---------------------------------------------------------------------------


def probe_server(
    record: Dict[str, Any], timeout: float = DEFAULT_CONNECT_TIMEOUT
) -> Dict[str, Any]:
    """Connect, initialize, list tools, and close. Never raises."""
    connection: Optional[_Connection] = None
    try:
        connection = _connection_for(record)
        connection.initialize(timeout)
        tools = connection.list_tools(timeout)
        return {
            "ok": True,
            "status": "connected",
            "protocol_version": connection.protocol_version,
            "tools": [
                {
                    "name": str(tool.get("name") or ""),
                    "description": str(tool.get("description") or ""),
                }
                for tool in tools
                if tool.get("name")
            ],
            "error": "",
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": "error",
            "protocol_version": "",
            "tools": [],
            "error": str(exc),
        }
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass


__all__ = [
    "McpError",
    "McpRuntime",
    "StdioConnection",
    "HttpConnection",
    "format_tool_result",
    "probe_server",
    "tool_name_for",
]
