"""
APRIL Backend API

Thin FastAPI adapter over the existing AgentRuntime and EventBus. This is the
HTTP/SSE surface that web/desktop frontends (and other systems) can use.

It is intentionally a shim: it does not re-implement agent logic, session state,
or event semantics. All of those continue to live in core.agent and core.events.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import os
import re
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict

from .events import get_event_bus, Event
from .browser_service import BrowserService
from .mcp_registry import McpRegistry, RegistryError, UnknownServer, DuplicateName

KNOWN_EVENT_TYPES = [
    "message.delta",
    "message.start",
    "message.done",
    "tool_call.start",
    "tool_call.output",
    "tool_call.done",
    "log",
    "error",
    "status",
    "session",
]


def event_to_dict(event: Event[Any]) -> dict:
    payload = event.payload
    if dataclasses.is_dataclass(payload):
        payload = dataclasses.asdict(payload)
    timestamp = getattr(event, "timestamp", None)
    if isinstance(timestamp, datetime):
        timestamp = timestamp.isoformat()
    return {
        "type": event.event_type,
        "payload": payload,
        "timestamp": timestamp,
        "id": event.id,
    }


class EventBroadcaster:
    """Fan-out bridge from the in-process EventBus to per-connection SSE queues."""

    def __init__(self) -> None:
        self._queues: List[asyncio.Queue] = []
        self._lock = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._subscriptions: List[str] = []

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        bus = get_event_bus()
        for event_type in KNOWN_EVENT_TYPES:
            sub_id = bus.subscribe(event_type, self._forward)
            self._subscriptions.append(sub_id)

    def stop(self) -> None:
        bus = get_event_bus()
        for sub_id in self._subscriptions:
            with suppress(Exception):
                bus.unsubscribe(sub_id)
        self._subscriptions.clear()

    def _forward(self, event: Event[Any]) -> None:
        if self._loop is None:
            return
        data = event_to_dict(event)
        for queue in list(self._queues):
            with suppress(Exception):
                self._loop.call_soon_threadsafe(queue.put_nowait, data)

    async def register(self, queue: asyncio.Queue) -> None:
        self._queues.append(queue)

    async def unregister(self, queue: asyncio.Queue) -> None:
        with suppress(ValueError):
            self._queues.remove(queue)


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None


class ClearRequest(BaseModel):
    session_id: Optional[str] = None


class BrowserOpenRequest(BaseModel):
    session_id: Optional[str] = None
    viewport_width: int = 1280
    viewport_height: int = 800


class BrowserNavigateRequest(BaseModel):
    session_id: str
    url: str
    wait_until: str = "domcontentloaded"
    timeout_ms: int = 30_000


class BrowserActionRequest(BaseModel):
    session_id: str
    action_type: str
    selector: Optional[str] = None
    text: Optional[str] = None
    key: Optional[str] = None
    timeout_ms: int = 30_000


class BrowserCloseRequest(BaseModel):
    session_id: str


class McpServerIn(BaseModel):
    """Create body. Pydantic forbids extras so typos fail with a 422 up front;
    deep validation (transport/command/url coherence) is the registry's job."""

    model_config = ConfigDict(extra="forbid")

    name: str
    transport: str
    command: Optional[str] = None
    args: Optional[List[str]] = None
    url: Optional[str] = None
    env: Optional[Dict[str, str]] = None
    enabled: bool = True


class McpServerPatch(BaseModel):
    """Partial update; every field optional, extras forbidden."""

    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = None
    transport: Optional[str] = None
    command: Optional[str] = None
    args: Optional[List[str]] = None
    url: Optional[str] = None
    env: Optional[Dict[str, str]] = None
    enabled: Optional[bool] = None


def create_default_agent():
    """Build an AgentRuntime from environment/config. May return None on failure."""
    try:
        from .config import AgentConfig
        from .agent import AgentRuntime

        config = AgentConfig.from_env()
        return AgentRuntime(config)
    except Exception as exc:  # pragma: no cover - depends on local env
        logging.getLogger("april.api").error("Failed to create default agent: %s", exc)
        return None


_SKILL_ROOTS = (".agents/skills", "skills")
_SKILL_NAME_RX = re.compile(r"^(?P<key>[\w.-]+):\s*(?P<value>.*)$", re.M)


def _parse_skill_frontmatter(text: str) -> Dict[str, str]:
    """Frontmatter `key: value` pairs between the first two `---` lines (pure)."""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    block = text[3:end]
    out: Dict[str, str] = {}
    for m in _SKILL_NAME_RX.finditer(block):
        value = m.group("value").strip().strip("'\"")
        if value:
            out.setdefault(m.group("key").strip(), value)
    return out


def _scan_skills(root: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Skill inventory the Skills screen lists (read-only, no side effects).

    Sources: the harness skills component surfaces — `.agents/skills/<name>/SKILL.md`
    and `skills/<name>/SKILL.md` (docs/backend-adapters/04) — plus provenance from
    the committed `skills-lock.json` when a lock entry names the same skill.
    Frontmatter without a description is kept but flagged, mirroring the docs'
    note that such skills are dead weight in the LLM path.
    """
    repo = Path(root) if root is not None else Path(__file__).resolve().parent.parent
    lock: Dict[str, Any] = {}
    lock_path = repo / "skills-lock.json"
    if lock_path.is_file():
        try:
            doc = json.loads(lock_path.read_text(encoding="utf-8"))
            lock = {str(k): v for k, v in (doc.get("skills") or {}).items()}
        except (OSError, json.JSONDecodeError):
            lock = {}

    seen: set[str] = set()
    skills: List[Dict[str, Any]] = []
    for base in _SKILL_ROOTS:
        base_dir = repo / base
        if not base_dir.is_dir():
            continue
        for skill_md in sorted(base_dir.glob("*/SKILL.md")):
            name = skill_md.parent.name
            if name in seen:
                continue
            seen.add(name)
            try:
                front = _parse_skill_frontmatter(skill_md.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                front = {}
            prov = lock.get(name) if isinstance(lock.get(name), dict) else {}
            skills.append(
                {
                    "name": front.get("name") or name,
                    "description": front.get("description") or "",
                    "path": str(skill_md.relative_to(repo)),
                    "has_description": bool(front.get("description")),
                    "frontmatter_matches_dir": (front.get("name") or name) == name,
                    "source": prov.get("source") or "",
                    "computed_hash": prov.get("computedHash") or "",
                }
            )
    return sorted(skills, key=lambda s: s["name"].lower())


def create_app(agent=None):
    app = FastAPI(title="APRIL Agent API", version="0.1.0")

    app.state.agent = agent
    app.state.broadcaster = None
    app.state.browser_service = None
    app.state.mcp_registry = None

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:4173",
            "http://127.0.0.1:4173",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.on_event("startup")
    async def _startup() -> None:
        loop = asyncio.get_event_loop()
        if app.state.agent is None:
            app.state.agent = create_default_agent()
        if app.state.browser_service is None:
            app.state.browser_service = BrowserService()
            await app.state.browser_service.start()
        if app.state.mcp_registry is None:
            app.state.mcp_registry = McpRegistry()
        if app.state.broadcaster is None:
            broadcaster = EventBroadcaster()
            broadcaster.start(loop)
            app.state.broadcaster = broadcaster

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        broadcaster = app.state.broadcaster
        if broadcaster is not None:
            broadcaster.stop()
        browser_service = app.state.browser_service
        if browser_service is not None:
            await browser_service.stop()
        agent = app.state.agent
        close = getattr(agent, "close", None)
        if callable(close):
            close()

    @app.get("/healthz")
    async def healthz() -> dict:
        agent = app.state.agent
        return {
            "status": "ok",
            "agent": agent is not None,
            "session_id": getattr(getattr(agent, "session", None), "id", None),
        }

    @app.get("/api/events")
    async def events(request: Request) -> StreamingResponse:
        broadcaster = request.app.state.broadcaster
        queue: asyncio.Queue = asyncio.Queue()

        async def stream():
            await broadcaster.register(queue)
            try:
                yield "event: connected\ndata: {\"status\": \"connected\"}\n\n"
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        item = await asyncio.wait_for(queue.get(), timeout=15.0)
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    yield f"event: {item['type']}\ndata: {json.dumps(item)}\n\n"
            finally:
                await broadcaster.unregister(queue)

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.post("/api/chat")
    async def chat(request: Request, body: ChatRequest) -> StreamingResponse:
        agent = request.app.state.agent
        if agent is None:
            raise RuntimeError("Agent is not available")

        async def stream():
            yield "event: chat.started\ndata: {\"status\": \"started\"}\n\n"
            try:
                async for token in agent.run_streaming(body.message):
                    yield "event: message.delta\n" + f"data: {json.dumps({'content': token})}\n\n"
                yield "event: chat.done\ndata: {\"status\": \"done\"}\n\n"
            except Exception as exc:  # pragma: no cover
                yield "event: error\ndata: " + json.dumps({"message": str(exc)}) + "\n\n"

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.post("/api/sessions/clear")
    async def clear(body: ClearRequest = ClearRequest()) -> dict:
        agent = app.state.agent
        if agent is not None:
            agent.clear_context()
        return {"status": "cleared"}

    @app.get("/api/context")
    async def context() -> dict:
        agent = app.state.agent
        if agent is None:
            return {"available": False}
        return {"available": True, **agent.get_context_summary()}

    def _browser_service() -> BrowserService:
        service = app.state.browser_service
        if service is None:
            raise RuntimeError("Browser service is not available")
        return service

    @app.post("/api/browser/open")
    async def browser_open(body: BrowserOpenRequest = BrowserOpenRequest()) -> dict:
        service = _browser_service()
        session = await service.open_session(
            session_id=body.session_id,
            viewport_width=body.viewport_width,
            viewport_height=body.viewport_height,
        )
        await service.navigate(session.id, "about:blank")
        return await service.get_state(session.id, include_screenshot=True)

    @app.post("/api/browser/navigate")
    async def browser_navigate(body: BrowserNavigateRequest) -> dict:
        service = _browser_service()
        state = await service.navigate(
            session_id=body.session_id,
            url=body.url,
            wait_until=body.wait_until,
            timeout_ms=body.timeout_ms,
        )
        return state

    @app.post("/api/browser/action")
    async def browser_action(body: BrowserActionRequest) -> dict:
        service = _browser_service()
        state = await service.action(
            session_id=body.session_id,
            action_type=body.action_type,
            selector=body.selector,
            text=body.text,
            key=body.key,
            timeout_ms=body.timeout_ms,
        )
        return state

    @app.get("/api/browser/screenshot/{session_id}")
    async def browser_screenshot(session_id: str) -> dict:
        service = _browser_service()
        return await service.get_state(session_id, include_screenshot=True)

    @app.get("/api/browser/content/{session_id}")
    async def browser_content(session_id: str) -> dict:
        service = _browser_service()
        return await service.get_state(session_id, include_screenshot=False)

    @app.post("/api/browser/close")
    async def browser_close(body: BrowserCloseRequest) -> dict:
        service = _browser_service()
        await service.close_session(body.session_id)
        return {"status": "closed", "session_id": body.session_id}

    @app.get("/api/browser/sessions")
    async def browser_sessions() -> dict:
        service = _browser_service()
        return {"sessions": await service.list_sessions()}

    # ------------------------------------------------------------------
    # Read-only discovery (P8 exception bundle A): ADE view contracts and
    # the skills inventory, exposed without any engine or behavior change.
    # ------------------------------------------------------------------

    def _ade_views():
        from .ade import views as ade_views

        return ade_views

    @app.get("/api/ade/surfaces")
    async def ade_surfaces() -> dict:
        return {"surfaces": _ade_views().surfaces_view()}

    @app.get("/api/ade/library")
    async def ade_library() -> dict:
        return {"library": _ade_views().library_view()}

    @app.get("/api/ade/persona/{name}")
    async def ade_persona(name: str) -> dict:
        ade_views = _ade_views()
        try:
            return {"available": True, "view": ade_views.persona_view(name)}
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f"Unknown persona: {name!r}")
        except Exception as exc:  # loud in the engine; view-level unavailability here
            raise HTTPException(
                status_code=404, detail=f"Persona view unavailable: {exc}"
            )

    @app.get("/api/skills")
    async def skills() -> dict:
        repo = Path(__file__).resolve().parent.parent
        return {"skills": _scan_skills(repo), "root": str(repo)}

    # ------------------------------------------------------------------
    # MCP server registry + runtime status: CRUD over the file-backed
    # registry, connection state for the inspector, and discovered tools.
    # ------------------------------------------------------------------

    def _mcp() -> McpRegistry:
        registry = app.state.mcp_registry
        if registry is None:  # pragma: no cover - startup guarantees this
            raise RuntimeError("MCP registry is not available")
        return registry

    def _mcp_error(exc: RegistryError) -> HTTPException:
        if isinstance(exc, UnknownServer):
            return HTTPException(status_code=404, detail=str(exc))
        if isinstance(exc, DuplicateName):
            return HTTPException(status_code=409, detail=str(exc))
        return HTTPException(status_code=422, detail=str(exc))

    def _agent_feature(name: str):
        features = getattr(app.state.agent, "features", None)
        getter = getattr(features, "get", None)
        return getter(name) if callable(getter) else None

    async def _refresh_mcp_runtime() -> None:
        feature = _agent_feature("mcp")
        refresh = getattr(feature, "refresh", None)
        if callable(refresh):
            try:
                await asyncio.to_thread(refresh, app.state.agent)
            except Exception as exc:  # connection errors are surfaced per server
                logging.getLogger("april.api").warning("MCP refresh failed: %s", exc)

    @app.get("/api/mcp/status")
    async def mcp_status() -> dict:
        feature = _agent_feature("mcp")
        status = getattr(feature, "status", None)
        if not callable(status):
            return {"available": False, "servers": [], "tools": [], "error": ""}
        return {"available": True, **status(app.state.agent)}

    @app.get("/api/tools")
    async def tools() -> dict:
        agent = app.state.agent
        if agent is None:
            return {"available": False, "tools": [], "mcp": {"servers": [], "tools": [], "error": ""}}
        features = getattr(agent, "features", None)
        schemas = []
        if features is not None:
            try:
                schemas = features.tool_schemas(agent)
            except Exception as exc:
                logging.getLogger("april.api").warning("tool schema build failed: %s", exc)
        tools_list = []
        for schema in schemas:
            function = schema.get("function") if isinstance(schema, dict) else None
            if isinstance(function, dict):
                tools_list.append(
                    {
                        "name": function.get("name", ""),
                        "description": function.get("description", ""),
                    }
                )
        mcp = _agent_feature("mcp")
        mcp_status_value = (
            mcp.status(agent) if callable(getattr(mcp, "status", None)) else {"servers": [], "tools": [], "error": ""}
        )
        return {"available": True, "tools": tools_list, "mcp": mcp_status_value}

    @app.get("/api/mcp/servers")
    async def mcp_list() -> dict:
        return {"servers": _mcp().list()}

    @app.post("/api/mcp/servers", status_code=201)
    async def mcp_create(body: McpServerIn) -> dict:
        try:
            record = _mcp().add(
                name=body.name,
                transport=body.transport,
                command=body.command,
                args=body.args,
                url=body.url,
                env=body.env,
                enabled=body.enabled,
            )
        except RegistryError as exc:
            raise _mcp_error(exc)
        await _refresh_mcp_runtime()
        return record

    @app.patch("/api/mcp/servers/{server_id}")
    async def mcp_update(server_id: str, body: McpServerPatch) -> dict:
        patch = {k: v for k, v in body.model_dump().items() if v is not None}
        try:
            record = _mcp().update(server_id, patch)
        except RegistryError as exc:
            raise _mcp_error(exc)
        await _refresh_mcp_runtime()
        return record

    @app.delete("/api/mcp/servers/{server_id}")
    async def mcp_delete(server_id: str) -> dict:
        try:
            _mcp().delete(server_id)
        except RegistryError as exc:
            raise _mcp_error(exc)
        await _refresh_mcp_runtime()
        return {"status": "deleted", "id": server_id}

    @app.post("/api/mcp/servers/{server_id}/test")
    async def mcp_test(server_id: str) -> dict:
        try:
            return _mcp().test(server_id)
        except RegistryError as exc:
            raise _mcp_error(exc)

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "core.api:app",
        host=os.getenv("APRIL_API_HOST", "127.0.0.1"),
        port=int(os.getenv("APRIL_API_PORT", "8000")),
        reload=False,
    )