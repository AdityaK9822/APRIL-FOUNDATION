"""Thin FastAPI front door over the Runtime.

Core routes: health, events (SSE + replay), chat (SSE), sessions, skills,
tools, MCP registry CRUD, and learning patterns. The old ADE/harness/browser
routes are retired with an explicit 410: the persona is now ``april/persona``
and the harness engine is ``april/learning``.
"""

import asyncio
import json
from typing import Optional
from uuid import uuid4

from .serialize import event_to_dict, session_payload


def create_app(runtime=None, *, registry=None, patterns=None):
    from fastapi import FastAPI, HTTPException, Query, Request
    from fastapi.responses import StreamingResponse
    from pydantic import BaseModel

    from ..memory.patterns import PatternStore
    from ..tools.mcp.registry import (
        DuplicateName,
        McpRegistry,
        RegistryError,
        UnknownServer,
    )

    class ChatIn(BaseModel):
        message: str
        session_id: Optional[str] = None

    class SessionIn(BaseModel):
        session_id: Optional[str] = None

    class StatusIn(BaseModel):
        status: str

    class McpIn(BaseModel):
        name: str
        transport: str = "stdio"
        command: Optional[str] = None
        args: Optional[list[str]] = None
        url: Optional[str] = None
        env: Optional[dict[str, str]] = None
        enabled: bool = True

    class McpPatch(BaseModel):
        name: Optional[str] = None
        transport: Optional[str] = None
        command: Optional[str] = None
        args: Optional[list[str]] = None
        url: Optional[str] = None
        env: Optional[dict[str, str]] = None
        enabled: Optional[bool] = None

    app = FastAPI(title="APRIL")
    app.state.runtime = runtime
    app.state.registry = registry or McpRegistry()
    app.state.patterns = patterns or PatternStore()
    app.state.current_session = "default"

    def _runtime():
        if app.state.runtime is None:
            from ..app import build
            from ..config import Settings

            app.state.runtime = build(Settings.from_env())
        return app.state.runtime

    def _source(name: str):
        for source in getattr(_runtime().tools, "sources", []):
            if getattr(source, "name", "") == name:
                return source
        return None

    def _mcp_error(exc: RegistryError) -> HTTPException:
        if isinstance(exc, UnknownServer):
            return HTTPException(status_code=404, detail=str(exc))
        if isinstance(exc, DuplicateName):
            return HTTPException(status_code=409, detail=str(exc))
        return HTTPException(status_code=422, detail=str(exc))

    def _refresh_mcp() -> None:
        source = _source("mcp")
        refresh = getattr(source, "refresh", None)
        if callable(refresh):
            try:
                refresh()
            except Exception:
                pass

    @app.get("/healthz")
    def healthz() -> dict:
        return {
            "status": "ok",
            "agent": app.state.runtime is not None,
            "session_id": app.state.current_session,
        }

    @app.get("/api/events")
    async def events(request: Request):
        bus = _runtime().events
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def forward(event):
            loop.call_soon_threadsafe(queue.put_nowait, event_to_dict(event))

        sub_id = bus.subscribe("*", forward)

        async def stream():
            yield 'event: connected\ndata: {"status": "connected"}\n\n'
            try:
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
                bus.unsubscribe(sub_id)

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.get("/api/events/history")
    def event_history(
        limit: int = 200, exclude: list[str] = Query(default=[])
    ) -> dict:
        if not 0 <= limit <= 1000:
            raise HTTPException(status_code=422, detail="limit must be within 0..1000")
        skipped = set(exclude)
        rows = [
            event_to_dict(event)
            for event in _runtime().events.get_history(limit=1000)
            if event.event_type not in skipped
        ]
        rows = rows[-limit:] if limit else []
        return {
            "events": rows,
            "count": len(rows),
            "types": sorted({row["type"] for row in rows}),
        }

    @app.post("/api/chat")
    async def chat(body: ChatIn):
        runtime = _runtime()
        session_id = body.session_id or app.state.current_session

        async def stream():
            yield 'event: chat.started\ndata: {"status": "started"}\n\n'
            try:
                reply = await asyncio.to_thread(
                    runtime.respond, body.message, session_id
                )
                yield (
                    "event: message.delta\ndata: "
                    + json.dumps({"content": reply})
                    + "\n\n"
                )
                yield 'event: chat.done\ndata: {"status": "done"}\n\n'
            except Exception as exc:
                yield "event: error\ndata: " + json.dumps({"message": str(exc)}) + "\n\n"

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.post("/api/sessions/clear")
    def clear() -> dict:
        app.state.current_session = f"session-{uuid4().hex[:8]}"
        return {"status": "cleared", "session_id": app.state.current_session}

    @app.get("/api/context")
    def context() -> dict:
        runtime = _runtime()
        session_id = app.state.current_session
        history = runtime.memory.history(session_id, limit=1000)
        info = runtime.memory.session(session_id)
        return {
            "available": True,
            "session_id": session_id,
            "message_count": len(history),
            "created_at": info.created_at if info else "",
            "tools_available": [schema.name for schema in runtime.tools.schemas()],
            **runtime.usage,
        }

    @app.get("/api/tokens")
    def tokens() -> dict:
        return {"session_id": app.state.current_session, **_runtime().usage}

    @app.get("/api/session/current")
    def session_current() -> dict:
        runtime = _runtime()
        return session_payload(
            app.state.current_session,
            runtime.memory.history(app.state.current_session, limit=200),
        )

    @app.get("/api/sessions")
    def sessions_list(limit: int = 100) -> dict:
        return {
            "sessions": _runtime().memory.sessions(limit=limit),
            "available": True,
        }

    @app.post("/api/sessions/new")
    def sessions_new() -> dict:
        app.state.current_session = f"session-{uuid4().hex[:8]}"
        return {"session_id": app.state.current_session, "status": "created"}

    @app.get("/api/session/{session_id}")
    def session_get(session_id: str, limit: int = 200) -> dict:
        return session_payload(
            session_id,
            _runtime().memory.history(session_id, limit=limit),
        )

    @app.post("/api/session/restore")
    def session_restore(body: SessionIn) -> dict:
        if not body.session_id:
            raise HTTPException(status_code=422, detail="session_id is required")
        app.state.current_session = body.session_id
        history = _runtime().memory.history(body.session_id, limit=10000)
        return {
            "session_id": body.session_id,
            "message_count": len(history),
            "status": "restored",
        }

    @app.get("/api/skills")
    def skills() -> dict:
        source = _source("skills")
        loader = getattr(source, "loader", None)
        if loader is None:
            return {"skills": [], "root": ""}
        return {
            "skills": [
                {
                    "name": skill.name,
                    "description": skill.description,
                    "scope": skill.scope,
                    "path": str(skill.path),
                }
                for skill in loader.discover()
            ],
            "root": str(getattr(loader, "workspace", "")),
        }

    @app.get("/api/tools")
    def tools() -> dict:
        runtime = _runtime()
        return {
            "available": True,
            "tools": [
                {"name": schema.name, "description": schema.description}
                for schema in runtime.tools.schemas()
            ],
        }

    @app.get("/api/mcp/status")
    def mcp_status() -> dict:
        source = _source("mcp")
        if source is None:
            return {"available": False, "servers": [], "tools": [], "error": ""}
        return {"available": True, **source.status()}

    @app.get("/api/mcp/servers")
    def mcp_list() -> dict:
        return {"servers": app.state.registry.list()}

    @app.post("/api/mcp/servers", status_code=201)
    def mcp_create(body: McpIn) -> dict:
        try:
            record = app.state.registry.add(
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
        _refresh_mcp()
        return record

    @app.patch("/api/mcp/servers/{server_id}")
    def mcp_update(server_id: str, body: McpPatch) -> dict:
        try:
            record = app.state.registry.update(
                server_id, body.model_dump(exclude_none=True)
            )
        except RegistryError as exc:
            raise _mcp_error(exc)
        _refresh_mcp()
        return record

    @app.delete("/api/mcp/servers/{server_id}")
    def mcp_delete(server_id: str) -> dict:
        try:
            app.state.registry.delete(server_id)
        except RegistryError as exc:
            raise _mcp_error(exc)
        _refresh_mcp()
        return {"status": "deleted", "id": server_id}

    @app.post("/api/mcp/servers/{server_id}/test")
    def mcp_test(server_id: str) -> dict:
        try:
            return app.state.registry.test(server_id)
        except RegistryError as exc:
            raise _mcp_error(exc)

    @app.get("/api/patterns")
    def patterns_list(
        session_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> dict:
        return {
            "patterns": app.state.patterns.patterns(
                session_id=session_id, status=status, limit=limit
            )
        }

    @app.patch("/api/patterns/{pattern_id}/status")
    def patterns_status(pattern_id: int, body: StatusIn) -> dict:
        try:
            app.state.patterns.set_status(pattern_id, body.status)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return {"id": pattern_id, "status": body.status}

    @app.get("/api/experience")
    def experience_retired():
        raise HTTPException(
            status_code=410, detail="retired; the experience patterns moved to /api/patterns"
        )

    @app.get("/api/ade/{path:path}")
    def ade_retired(path: str):
        raise HTTPException(
            status_code=410,
            detail="retired; the ADE dissolved into april/persona and april/learning",
        )

    @app.get("/api/harness/{path:path}")
    def harness_retired(path: str):
        raise HTTPException(
            status_code=410,
            detail="retired; the harness engine is april/learning",
        )

    @app.get("/api/browser/{path:path}")
    def browser_retired(path: str):
        raise HTTPException(status_code=410, detail="retired; not part of the new stack")

    return app
