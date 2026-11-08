"""P8 wiring — HTTP endpoint pass for the approved backend exception set.

Pass 2 of the two-verification contract. Two different transports, zero new
dependencies:

* `_asgi_call` drives the FastAPI app in-process through the raw ASGI interface
  (stdlib asyncio) — routing + validation, no sockets.
* Running this file as a script (``python3 tests/test_web_extensions.py``)
  additionally launches the real uvicorn server and asserts against it over
  loopback TCP via urllib — the same path the web dev server (vite proxy) uses.

Browser endpoints are exercised only in the live TCP pass (they need the real
Playwright/Chromium fallback the backend owns); the ASGI pass covers everything
else, since state objects like the browser service stay None without lifespan.

Run (logic passes):      .venv/bin/python -m pytest tests/test_web_extensions.py -v
Run (transport passes):  .venv/bin/python tests/test_web_extensions.py
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parent.parent
if __package__ in (None, ""):  # script mode: make `core` importable before its import below
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))

from core.api import _scan_skills, create_app
from core.mcp_registry import McpRegistry


# --- ASGI driver (stdlib, no httpx/testclient) ------------------------------


def _asgi_call(app, method: str, path: str, body: dict | None = None):
    payload = None if body is None else json.dumps(body).encode()
    headers = []
    if payload is not None:
        headers = [(b"content-type", b"application/json")]
    sent = {"done": False}
    collected = {"status": None, "body": bytearray(), "headers": []}

    async def run() -> None:
        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "root_path": "",
            "headers": headers,
            "client": ("testclient", 1),
            "server": ("testserver", 80),
        }

        async def receive():
            if payload is not None and not sent["done"]:
                sent["done"] = True
                return {"type": "http.request", "body": payload, "more_body": False}
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.start":
                collected["status"] = message["status"]
                collected["headers"] = message.get("headers", [])
            elif message["type"] == "http.response.body":
                collected["body"].extend(message.get("body", b""))

        await app(scope, receive, send)

    asyncio.run(run())
    raw = bytes(collected["body"])
    try:
        parsed = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        parsed = None
    return collected["status"], parsed, raw


@pytest.fixture()
def app():
    application = create_app(agent=None)  # no startup: browser/agent stay None
    return application


@pytest.fixture()
def mcp_app(tmp_path):
    application = create_app(agent=None)
    application.state.mcp_registry = McpRegistry(tmp_path / "mcp_servers.json")
    return application


# --- health & discovery endpoints (ASGI) ------------------------------------


def test_healthz_reports_agent_absence(app):
    status, body, raw = _asgi_call(app, "GET", "/healthz")
    assert status == 200 and body == {"status": "ok", "agent": False, "session_id": None}
    assert b"ok" in raw  # physical bytes, not just parsed dicts


def test_skills_endpoint_lists_the_committed_skill(app):
    status, body, _ = _asgi_call(app, "GET", "/api/skills")
    assert status == 200
    names = [s["name"] for s in body["skills"]]
    assert "find-animation-opportunities" in names
    entry = body["skills"][names.index("find-animation-opportunities")]
    assert entry["description"], "frontmatter description must survive the scan"
    assert entry["source"] == "emilkowalski/skills"  # provenance from skills-lock.json
    assert entry["frontmatter_matches_dir"] is True
    assert entry["path"].endswith("/SKILL.md")


def test_scan_skills_is_pure_over_a_fake_repo(tmp_path):
    """Filesystem-level restatement (different logic than the endpoint above):
    invent a repo tree, let the scanner read only what is on disk."""
    skills = tmp_path / ".agents" / "skills" / "alpha"
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: Demo skill for the scanner.\n---\n# Body\n"
    )
    (tmp_path / "skills-lock.json").write_text(
        json.dumps({"version": 1, "skills": {"alpha": {"source": "somewhere/x"}}})
    )
    bare = tmp_path / ".agents" / "skills" / "bodied"
    bare.mkdir(parents=True)
    (bare / "SKILL.md").write_text("---\nname: bodied\n---\n# no description\n")

    found = {s["name"]: s for s in _scan_skills(tmp_path)}
    assert found["alpha"]["description"] == "Demo skill for the scanner."
    assert found["alpha"]["source"] == "somewhere/x"
    assert found["alpha"]["has_description"] is True
    assert found["bodied"]["has_description"] is False  # kept, flagged, not dropped
    assert found["bodied"]["description"] == ""


def test_ade_surfaces_shape(app):
    status, body, _ = _asgi_call(app, "GET", "/api/ade/surfaces")
    assert status == 200
    surfaces = {s["name"]: s for s in body["surfaces"]}
    assert set(surfaces) == {"april", "zcode"}
    for s in surfaces.values():
        assert isinstance(s["always_on"], list)
        assert s["skill_dir"] and isinstance(s["always_on_max"], int)
        assert "reject_empty_always_on" in s
    # same skills vocabulary, different always-on shape (AGENTS.md folding)
    assert surfaces["april"]["skill_dir"] == surfaces["zcode"]["skill_dir"]
    assert surfaces["zcode"]["always_on"] == ["AGENTS.md"]
    assert len(surfaces["april"]["always_on"]) > 1


def test_ade_library_shape(app):
    status, body, _ = _asgi_call(app, "GET", "/api/ade/library")
    assert status == 200 and isinstance(body["library"], list)
    for row in body["library"]:
        assert {"persona", "domain", "entries"} <= set(row)


def test_ade_persona_april_contract(app):
    status, body, _ = _asgi_call(app, "GET", "/api/ade/persona/april")
    assert status == 200
    view = body["view"]
    # Exact key contract — facts-text can never enter the payload because the
    # view exposes only a fact COUNT (persona_view's documented invariant).
    assert set(view) == {
        "persona",
        "domain",
        "source",
        "temperature",
        "system_prompt_chars",
        "guardrail_chars",
        "attested_rules",
        "machine_rules",
        "user_context_facts",
        "fingerprint",
        "deployed_dir",
        "deployments",
        "library",
    }
    assert view["persona"] == "april"
    assert view["source"].startswith(("deployed:", "profile:"))
    assert isinstance(view["user_context_facts"], int)
    assert isinstance(view["temperature"], float) and 0.0 < view["temperature"] < 2.0


def test_ade_persona_unknown_is_404_not_500(app):
    status, body, _ = _asgi_call(app, "GET", "/api/ade/persona/not-a-real-persona")
    assert status == 404 and body and "not-a-real-persona" in body["detail"]


# --- MCP CRUD endpoints (ASGI) ---------------------------------------------


SERVER = {
    "name": "Filesystem",
    "transport": "stdio",
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
}
SERVER_HTTP = {"name": "Weather", "transport": "http", "url": "https://weather/mcp"}


def test_mcp_empty_then_create_then_list(mcp_app):
    assert _asgi_call(mcp_app, "GET", "/api/mcp/servers")[1] == {"servers": []}
    status, record, _ = _asgi_call(mcp_app, "POST", "/api/mcp/servers", SERVER)
    assert status == 201
    assert record["id"] == "filesystem"
    status, listing, _ = _asgi_call(mcp_app, "GET", "/api/mcp/servers")
    assert [s["id"] for s in listing["servers"]] == ["filesystem"]


def test_mcp_create_duplicate_is_409(mcp_app):
    _, first, _ = _asgi_call(mcp_app, "POST", "/api/mcp/servers", SERVER)
    # identical record: same name → 409 at the identity check
    status, body, _ = _asgi_call(mcp_app, "POST", "/api/mcp/servers", dict(SERVER))
    assert status == 409 and "already exists" in body["detail"]
    # validation precedes identity: a duplicate with a broken shape is a 422
    status, body, _ = _asgi_call(mcp_app, "POST", "/api/mcp/servers", dict(SERVER, transport="http"))
    assert status == 422 and "url" in body["detail"]


def test_mcp_create_invalid_and_extra_fields_are_422(mcp_app):
    status, body, _ = _asgi_call(
        mcp_app, "POST", "/api/mcp/servers", {"name": "B", "transport": "http"}
    )
    assert status == 422
    status, body, _ = _asgi_call(
        mcp_app, "POST", "/api/mcp/servers", dict(SERVER, wat=1)
    )
    assert status == 422  # pydantic extra=forbid surfaced before the registry


def test_mcp_patch_toggles_and_validates(mcp_app):
    status, record, _ = _asgi_call(mcp_app, "POST", "/api/mcp/servers", SERVER)
    sid = record["id"]
    status, patched, _ = _asgi_call(
        mcp_app, "PATCH", f"/api/mcp/servers/{sid}", {"enabled": False}
    )
    assert status == 200 and patched["enabled"] is False
    status, body, _ = _asgi_call(mcp_app, "PATCH", f"/api/mcp/servers/{sid}", {"transport": "tcp"})
    assert status == 422
    status, body, _ = _asgi_call(mcp_app, "PATCH", "/api/mcp/servers/ghost", {"enabled": True})
    assert status == 404


def test_mcp_test_endpoint_validates_only(mcp_app):
    _asgi_call(mcp_app, "POST", "/api/mcp/servers", SERVER)
    status, report, _ = _asgi_call(mcp_app, "POST", "/api/mcp/servers/filesystem/test")
    assert status == 200 and report["ok"] is True and report["client"] == "not-connected"
    status, _, _ = _asgi_call(mcp_app, "POST", "/api/mcp/servers/ghost/test")
    assert status == 404


def test_mcp_delete_then_missing(mcp_app):
    _asgi_call(mcp_app, "POST", "/api/mcp/servers", SERVER_HTTP)
    status, body, _ = _asgi_call(mcp_app, "DELETE", "/api/mcp/servers/weather")
    assert status == 200 and body["status"] == "deleted"
    status, _, _ = _asgi_call(mcp_app, "DELETE", "/api/mcp/servers/weather")
    assert status == 404
    # registry file on the chosen tmp path was actually written and re-read
    assert _asgi_call(mcp_app, "GET", "/api/mcp/servers")[1]["servers"] == []


def test_mcp_env_roundtrip(mcp_app):
    status, record, _ = _asgi_call(
        mcp_app, "POST", "/api/mcp/servers",
        dict(SERVER, env={"FS_ROOT": "/data", "DEBUG": "1"}),
    )
    assert status == 201
    _, listing, _ = _asgi_call(mcp_app, "GET", "/api/mcp/servers")
    assert listing["servers"][0]["env"] == {"FS_ROOT": "/data", "DEBUG": "1"}


# --- MCP runtime status (inspector/tools surface) ---------------------------


def test_mcp_status_without_agent_is_unavailable(app):
    status, body, _ = _asgi_call(app, "GET", "/api/mcp/status")
    assert status == 200
    assert body == {"available": False, "servers": [], "tools": [], "error": ""}


def test_tools_endpoint_reports_tool_schemas_and_mcp_status():
    runtime_status = {
        "error": "",
        "servers": [
            {
                "id": "fake",
                "name": "Fake",
                "transport": "stdio",
                "enabled": True,
                "status": "error",
                "error": "connection refused",
                "protocol_version": "",
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

    class FakeMcp:
        def status(self, agent=None):
            return runtime_status

    class FakeFeatures:
        def get(self, name):
            return mcp if name == "mcp" else None

        def tool_schemas(self, agent):
            return [
                {
                    "type": "function",
                    "function": {
                        "name": "shell_execute",
                        "description": "Run a shell command.",
                    },
                }
            ]

    mcp = FakeMcp()
    agent = SimpleNamespace(features=FakeFeatures(), config=SimpleNamespace())
    application = create_app(agent=agent)

    status, body, _ = _asgi_call(application, "GET", "/api/mcp/status")
    assert status == 200 and body["available"] is True
    assert body["servers"][0]["status"] == "error"
    assert body["servers"][0]["error"] == "connection refused"
    assert body["servers"][0]["tools"][0]["exposed_name"] == "mcp__fake__echo"

    status, body, _ = _asgi_call(application, "GET", "/api/tools")
    assert status == 200 and body["available"] is True
    assert body["tools"] == [
        {"name": "shell_execute", "description": "Run a shell command."}
    ]
    assert body["mcp"]["servers"][0]["status"] == "error"


# --- live TCP passes (manual: python3 tests/test_web_extensions.py) ----------


def _live(method: str, path: str, body: dict | None = None):
    request = urllib.request.Request(
        f"http://127.0.0.1:8011{path}",
        method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode() or "null")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode() or "null")


def _check(label: str, got, want):
    assert got == want, f"{label}: expected {want!r}, got {got!r}"
    print(f"  ok: {label}")


def live_pass() -> None:
    """The real wire: uvicorn boots the full app (agent + browser + registry), and
    every approved endpoint is asserted over loopback TCP. Browser endpoints are
    included here because they need the backend's real Chromium fallback."""
    print("live transport pass (uvicorn :8011)…")
    with tempfile.TemporaryDirectory() as td:
        env = {
            **os.environ,
            "APRIL_MCP_REGISTRY": str(Path(td) / "mcp_servers.json"),
            "APRIL_EXECUTOR": "dry",
        }
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "core.api:app", "--port", "8011", "--log-level", "warning"],
            cwd=str(REPO),
            env=env,
            stdout=subprocess.PIPE,
        )
        try:
            deadline = time.time() + 30
            while True:
                try:
                    _live("GET", "/healthz")
                    break
                except (urllib.error.URLError, ConnectionError, OSError):
                    if time.time() > deadline or proc.poll() is not None:
                        raise AssertionError("uvicorn never came up")
                    time.sleep(0.5)

            _check("healthz", _live("GET", "/healthz")[0], 200)

            status, body = _live("GET", "/api/skills")
            assert status == 200 and any(
                s["name"] == "find-animation-opportunities" for s in body["skills"]
            )
            print("  ok: /api/skills lists the committed skill")

            status, body = _live("GET", "/api/ade/surfaces")
            assert status == 200 and {s["name"] for s in body["surfaces"]} == {"april", "zcode"}
            print("  ok: /api/ade/surfaces returns both surfaces")

            status, body = _live("GET", "/api/ade/library")
            assert status == 200 and isinstance(body["library"], list)
            print("  ok: /api/ade/library")

            status, body = _live("GET", "/api/ade/persona/april")
            assert status == 200 and body["view"]["persona"] == "april"
            assert isinstance(body["view"]["user_context_facts"], int)
            print("  ok: /api/ade/persona/april (facts as count only)")

            _check("ade persona 404", _live("GET", "/api/ade/persona/zz-none")[0], 404)

            uid = f"live-{uuid.uuid4().hex[:6]}"
            status, rec = _live("POST", "/api/mcp/servers", {"name": uid, "transport": "stdio", "command": "echo"})
            assert status == 201 and rec["id"].startswith("live-")
            sid = rec["id"]
            _check("mcp patch", _live("PATCH", f"/api/mcp/servers/{sid}", {"enabled": False})[1]["enabled"], False)
            _check("mcp test", _live("POST", f"/api/mcp/servers/{sid}/test")[1]["client"], "not-connected")
            _check("mcp delete", _live("DELETE", f"/api/mcp/servers/{sid}")[1]["status"], "deleted")
            _check("mcp deleted 404", _live("DELETE", f"/api/mcp/servers/{sid}")[0], 404)
            _check("mcp limit: dup 409", _live("POST", "/api/mcp/servers", {"name": "dupx", "transport": "http", "url": "http://a/b"})[0], 201)
            _check("mcp limit enforced", _live("POST", "/api/mcp/servers", {"name": "dupx", "transport": "http", "url": "http://c/d"})[0], 409)

            status, browser = _live("POST", "/api/browser/open", {})
            assert status == 200 and browser.get("session_id")
            bid = browser["session_id"]
            status, nav = _live("POST", "/api/browser/navigate", {"session_id": bid, "url": "about:blank"})
            assert status == 200 and nav.get("url") == "about:blank"
            status, shot = _live("GET", f"/api/browser/screenshot/{bid}")
            assert status == 200 and shot.get("screenshot", {}).get("base64")
            status, sess = _live("GET", "/api/browser/sessions")
            assert status == 200 and bid in sess["sessions"]
            _check("browser close", _live("POST", "/api/browser/close", {"session_id": bid})[1]["status"], "closed")

            _check("sessions/clear", _live("POST", "/api/sessions/clear", {})[1]["status"], "cleared")
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover
                proc.kill()
    print("live transport pass: all ok")


if __name__ == "__main__":
    try:
        live_pass()
    except AssertionError as exc:
        print(f"live transport pass FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
    print("asgi pass follows…")
    sys.exit(pytest.main([__file__, "-v", "--tb=short"]))
