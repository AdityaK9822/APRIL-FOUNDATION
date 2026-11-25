"""Offline self-check for the new stack: no model, no network, no device.

Run: python3 -m april
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread

from april.app import build
from april.body import ESP32Devices, Device, FakeDevices
from april.config import Settings
from april.events import EventBus, ErrorEvent, FileSink, LogEvent, Logger
from april.learning import (
    DeviceTarget,
    DryProposer,
    Engine,
    InMemoryLessonStore,
    MemoryTarget,
    Proposer,
    ProposerError,
    Trace,
)
from april.llm import ChatReply, ChatRequest, FakeModel, Model, ToolCall
from april.memory import (
    InMemoryMemoryStore,
    MemoryExtractor,
    MemoryInput,
    PatternStore,
    SQLiteMemory,
    Turn,
    analyze_from_event_memory,
)
from april.persona import FileProfiles, Persona, resolve
from april.persona import kernel as persona_kernel
from april.runtime import Runtime
from april.tools import (
    BuiltinSource,
    HookEvent,
    HookRunner,
    HookSet,
    HookSpec,
    InstallError,
    Marketplace,
    McpRegistry,
    McpSource,
    PluginCatalog,
    ProcessSubagentRunner,
    SkillLoader,
    SkillsSource,
    SubagentManager,
    SubagentRunner,
    SubagentsSource,
    TokenProvider,
    Toolset,
    ToolsetFeatureAdapter,
    TrustPolicy,
    discover_plugins,
    install,
    installed,
    load_hooks,
    parse_profile,
    uninstall,
    validate_server,
)
from april.tools.mcp.client import HttpConnection

FAKE_MCP = r'''
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
        result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        text = message.get("params", {}).get("arguments", {}).get("text", "")
        result = {"content": [{"type": "text", "text": text}], "isError": False}
    else:
        result = {}
    print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}), flush=True)
'''

BLOCK_HOOK = (
    "import json,sys;"
    "json.load(sys.stdin);"
    "print(json.dumps({'decision':'block','reason':'no'}))"
)


def _request() -> ChatRequest:
    return ChatRequest(model="test", messages=[{"role": "user", "content": "hi"}])


def _check_llm() -> None:
    settings = Settings.from_env()
    assert settings.model, "OPENAI_MODEL is empty"
    print("ok: settings")

    model = FakeModel([ChatReply(text="hello world")])
    assert isinstance(model, Model)
    print("ok: fake satisfies Model")

    reply = model.complete(_request())
    assert reply.text == "hello world", reply.text
    assert model.requests[-1].messages == [{"role": "user", "content": "hi"}]
    print("ok: complete")

    async def collect():
        return [delta async for delta in model.stream(_request())]

    deltas = asyncio.run(collect())
    text = "".join(delta.text or "" for delta in deltas)
    assert text == "hello world", text
    assert deltas[-1].finish_reason == "stop"
    print("ok: stream")


def _check_events() -> None:
    bus = EventBus(max_history=10)
    seen = []
    bus.subscribe("error", seen.append)
    event = bus.emit_error("test", "boom")
    assert event.event_type == "error"
    assert isinstance(event.payload, ErrorEvent)
    assert seen and seen[0] is event
    assert bus.get_history("error") == [event]
    print("ok: event dispatch")

    try:
        bus.emit(ErrorEvent(error_type="x", message="y"))
        raise AssertionError("payload-as-event was accepted")
    except TypeError:
        pass
    print("ok: payload-as-event refused")

    for i in range(15):
        bus.emit("log", LogEvent(level="INFO", source="t", message=str(i)))
    assert len(bus.get_history()) == 10
    print("ok: ring buffer")

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "april.log")
        FileSink(path).attach(bus)
        Logger(bus).info("hello")
        assert "hello" in open(path, encoding="utf-8").read()
    print("ok: logger + file sink")


def _check_memory() -> None:
    store = InMemoryMemoryStore()
    store.remember(Turn("s1", "user", "I use VS Code"))
    store.remember(Turn("s1", "assistant", "Noted"))
    assert len(store.history("s1")) == 2
    first = store.add_memory(
        MemoryInput(memory_type="fact", content={"text": "User uses VS Code"}, tags=["editor"])
    )
    second = store.add_memory(
        MemoryInput(memory_type="fact", content={"text": "User uses VS Code"})
    )
    assert first == second
    assert store.recall("VS Code")[0].content["text"] == "User uses VS Code"
    session = store.session("s1")
    assert session and session.message_count == 2
    pending = store.unprocessed("s1")
    assert len(pending) == 2
    store.mark_processed([turn["id"] for turn in pending])
    assert store.unprocessed("s1") == []
    print("ok: in-memory store")

    with tempfile.TemporaryDirectory() as tmp:
        db = SQLiteMemory(os.path.join(tmp, "mem.db"))
        db.remember(Turn("s2", "user", "hello"))
        memory_id = db.add_memory(
            MemoryInput(memory_type="fact", content={"text": "hello world"})
        )
        assert (
            db.add_memory(MemoryInput(memory_type="fact", content={"text": "hello world"}))
            == memory_id
        )
        assert db.recall("hello")[0].content["text"] == "hello world"
        session = db.session("s2")
        assert session and session.message_count == 1
        db.close()
    print("ok: sqlite store")

    llm = FakeModel(
        [
            ChatReply(
                text='{"extractions":[{"type":"fact","content":{"text":"x"},'
                '"confidence":0.9,"tags":["t"]}]}'
            )
        ]
    )
    extractor = MemoryExtractor(llm=llm, model="test")
    extracted = extractor.extract([{"role": "user", "content": "x"}])
    assert extracted and extracted[0]["type"] == "fact"
    print("ok: extractor")

    with tempfile.TemporaryDirectory() as tmp:
        patterns = PatternStore(os.path.join(tmp, "patterns.db"))
        pattern_id = patterns.record_pattern("s1", ["read", "shell"], "read + shell")
        assert patterns.patterns()[0]["tools"] == ["read", "shell"]
        patterns.set_status(pattern_id, "approved")
        assert patterns.patterns(status="approved")[0]["id"] == pattern_id
        bus = EventBus()
        bus.emit_tool_call_start("shell", {}, "1")
        bus.emit_tool_call_start("read", {}, "2")
        assert analyze_from_event_memory(patterns, bus, session_id="s1") == pattern_id
        patterns.close()
    print("ok: pattern store")


def _check_tools() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        builtin = BuiltinSource(workspace=tmp_path)
        result = builtin.dispatch("shell_execute", {"command": "printf hi"})
        assert result.ok and "hi" in result.content
        assert builtin.dispatch("write_file", {"path": "a.txt", "content": "hello"}).ok
        assert builtin.dispatch("read_file", {"path": "a.txt"}).content == "hello"
        assert "a.txt" in builtin.dispatch("list_dir", {"path": "."}).content
        escape = builtin.dispatch("read_file", {"path": "../outside"})
        assert not escape.ok
        print("ok: builtin tools")

        user_skills = tmp_path / "user_skills"
        module_skills = tmp_path / "module_skills"
        (user_skills / "ui").mkdir(parents=True)
        (user_skills / "ui" / "SKILL.md").write_text(
            "---\nname: ui\ndescription: UI design\nscope: user\n---\n\nUse grids.\n",
            encoding="utf-8",
        )
        (module_skills / "learning").mkdir(parents=True)
        (module_skills / "learning" / "SKILL.md").write_text(
            "---\nname: learning\ndescription: Operate the engine\n"
            "scope: module:learning\n---\n\nRead the ledger.\n",
            encoding="utf-8",
        )
        (module_skills / "evil").mkdir(parents=True)
        (module_skills / "evil" / "SKILL.md").write_text(
            "---\nname: evil\ndescription: x\nscope: user\n---\nbody\n",
            encoding="utf-8",
        )
        outside = tmp_path / "outside_skills" / "outside"
        outside.mkdir(parents=True)
        (outside / "SKILL.md").write_text(
            "---\nname: outside\ndescription: x\n---\n", encoding="utf-8"
        )
        (user_skills / "escape").symlink_to(outside, target_is_directory=True)

        loader = SkillLoader(
            user_roots=[user_skills],
            module_roots={"learning": module_skills, "evil": module_skills},
        )
        found = {skill.name for skill in loader.discover({"learning"})}
        assert {"ui", "learning"} <= found
        assert "evil" not in found and "outside" not in found
        assert "learning" not in {skill.name for skill in loader.discover(set())}
        skills = SkillsSource(loader, {"learning"})
        assert "load_skill" in {schema.name for schema in skills.schemas()}
        assert "Use grids" in skills.dispatch("load_skill", {"name": "ui"}).content
        print("ok: skills + scope + symlink")

        blocking = HookRunner(
            HookSet(
                [
                    HookSpec(
                        event="PreToolUse",
                        command=sys.executable,
                        args=("-c", BLOCK_HOOK),
                        kind="process",
                    )
                ]
            )
        )
        decision = blocking.fire(HookEvent.PRE_TOOL_USE, {"tool_name": "read_file"})
        assert not decision.allow and "no" in decision.reason

        exiting = HookRunner(
            HookSet(
                [
                    HookSpec(
                        event="PostToolUse",
                        command=sys.executable,
                        args=("-c", "import sys; sys.exit(2)"),
                        kind="process",
                    )
                ]
            )
        )
        assert not exiting.fire("PostToolUse", {"tool_name": "read_file"}).allow

        hooks_path = tmp_path / "hooks.json"
        hooks_path.write_text(
            json.dumps(
                {
                    "hooks": {
                        "enabled": True,
                        "events": {
                            "PreToolUse": [
                                {
                                    "hooks": [
                                        {
                                            "type": "process",
                                            "command": sys.executable,
                                            "args": ["-c", BLOCK_HOOK],
                                        }
                                    ]
                                }
                            ]
                        },
                    }
                }
            ),
            encoding="utf-8",
        )
        assert load_hooks(hooks_path, TrustPolicy([tmp_path])).hooks
        untrusted = load_hooks(hooks_path, TrustPolicy([], allow_workspace=False))
        assert not untrusted.trusted and not untrusted.hooks

        hooked = Toolset([BuiltinSource(workspace=tmp_path)], hooks=blocking)
        assert not hooked.dispatch("read_file", {"path": "a.txt"}).ok
        print("ok: hooks + trust")

        plugin_dir = tmp_path / "myplugin"
        (plugin_dir / ".zcode-plugin").mkdir(parents=True)
        (plugin_dir / ".zcode-plugin" / "plugin.json").write_text(
            json.dumps(
                {
                    "name": "myplugin",
                    "description": "test",
                    "mcpServers": {
                        "search": {"transport": "http", "url": "http://localhost:9"}
                    },
                    "skills": ["skills"],
                    "agents": ["agents"],
                }
            ),
            encoding="utf-8",
        )
        plugins = discover_plugins([plugin_dir])
        assert plugins and plugins[0].name == "myplugin"
        servers = PluginCatalog(plugins).mcp_servers()
        assert "plugin:myplugin:search" in servers
        print("ok: plugins")

        agents = tmp_path / "agents"
        agents.mkdir()
        (agents / "writer.md").write_text(
            "---\nname: writer\ndescription: Writes docs\ntools: ['*']\n---\n"
            "You write docs.\n",
            encoding="utf-8",
        )
        profile = parse_profile(agents / "writer.md", source="project")
        assert profile and profile.name == "writer"
        assert profile.system_prompt == "You write docs."
        sub_tools = Toolset([BuiltinSource(workspace=tmp_path)])
        runner = SubagentRunner(
            model=FakeModel([ChatReply(text="done")]),
            tools=sub_tools,
            default_model="test",
        )
        subagents = SubagentsSource([profile], runner)
        delegated = subagents.dispatch("run_subagent", {"name": "writer", "task": "write"})
        assert delegated.ok and delegated.content == "done"
        print("ok: subagents")

        registry = McpRegistry(tmp_path / "mcp_servers.json")
        script = tmp_path / "fake_mcp.py"
        script.write_text(FAKE_MCP, encoding="utf-8")
        registry.add(
            name="Fake",
            transport="stdio",
            command=sys.executable,
            args=[str(script)],
        )
        mcp = McpSource(registry=registry)
        mcp.start()
        try:
            assert "mcp__fake__echo" in {schema.name for schema in mcp.schemas()}
            echoed = mcp.dispatch("mcp__fake__echo", {"text": "hello"})
            assert echoed and echoed.ok and echoed.content == "hello"
        finally:
            mcp.close()
        print("ok: mcp stdio")

        toolset = Toolset([BuiltinSource(workspace=tmp_path)])
        assert {"shell_execute", "read_file", "write_file", "list_dir"} <= {
            schema.name for schema in toolset.schemas()
        }
        assert not toolset.dispatch("nope", {}).ok
        print("ok: toolset aggregation")

        unconfirmed = skills.dispatch(
            "create_skill",
            {"name": "made", "description": "d", "body": "b", "scope": "user"},
        )
        assert not unconfirmed.ok and "CONFIRMATION" in unconfirmed.content
        created = skills.dispatch(
            "create_skill",
            {
                "name": "made",
                "description": "d",
                "body": "b",
                "scope": "user",
                "confirm": True,
            },
        )
        assert created.ok
        assert "made" in {skill.name for skill in loader.discover(set())}
        assert skills.dispatch(
            "update_skill", {"name": "made", "description": "d2", "confirm": True}
        ).ok
        assert skills.dispatch("delete_skill", {"name": "made", "confirm": True}).ok
        assert "made" not in {skill.name for skill in loader.discover(set())}

        external = tmp_path / "external_skills" / "extskill"
        external.mkdir(parents=True)
        (external / "SKILL.md").write_text(
            "---\nname: extskill\ndescription: external\n---\nbody\n",
            encoding="utf-8",
        )
        importer = SkillLoader(
            user_roots=[tmp_path / "import_user"],
            external_roots=[("ext", tmp_path / "external_skills")],
        )
        assert importer.list_external("ext").startswith("[")
        assert importer.import_skill(
            "extskill", source="ext", scope="user", confirm=True
        ).startswith("Imported")
        assert (tmp_path / "import_user" / "extskill" / "SKILL.md").is_file()
        print("ok: skills CRUD + import")

        async_runner = HookRunner(
            HookSet(
                [
                    HookSpec(
                        event="Stop",
                        command=sys.executable,
                        args=("-c", "import time; time.sleep(0.5)"),
                        kind="process",
                        async_=True,
                    )
                ]
            )
        )
        started = time.time()
        assert async_runner.fire("Stop", {}).allow
        assert time.time() - started < 0.3
        print("ok: async hooks")

        manager = SubagentManager(runner)
        task_id = manager.start(profile, "write")
        state = manager.wait(task_id, timeout=5)
        assert state and state.status == "done" and state.result == "done"
        assert manager.status(task_id) is state
        os.environ["APRIL_SUBAGENT_FAKE"] = "1"
        try:
            isolated = ProcessSubagentRunner(workspace=tmp_path)
            assert isolated.run(profile, "write") == "done"
        finally:
            os.environ.pop("APRIL_SUBAGENT_FAKE", None)
        print("ok: subagent background + process isolation")

        class TokenHandler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(length)
                body = json.dumps(
                    {"access_token": "tok", "expires_in": 3600}
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), TokenHandler)
        Thread(target=server.serve_forever, daemon=True).start()
        try:
            record = {
                "name": "oauth",
                "transport": "http",
                "url": "http://127.0.0.1:9/mcp",
                "oauth": {
                    "grant": "client_credentials",
                    "client_id": "c",
                    "client_secret": "s",
                    "token_url": f"http://127.0.0.1:{server.server_port}/token",
                },
            }
            assert validate_server(record) == []
            assert TokenProvider().token(record) == "tok"
            assert HttpConnection(record)._headers()["Authorization"] == "Bearer tok"
        finally:
            server.shutdown()
        print("ok: mcp oauth")

        plugin_zip = tmp_path / "myplugin.zip"
        with zipfile.ZipFile(plugin_zip, "w") as zf:
            zf.writestr(
                "myplugin/.zcode-plugin/plugin.json",
                json.dumps({"name": "myplugin"}),
            )
        install_root = tmp_path / "installed_plugins"
        assert install(plugin_zip, install_root).name == "myplugin"
        assert installed(install_root)[0].name == "myplugin"
        assert uninstall("myplugin", install_root)

        evil_zip = tmp_path / "evil.zip"
        with zipfile.ZipFile(evil_zip, "w") as zf:
            zf.writestr("../evil.txt", "x")
            zf.writestr(
                "evil/.zcode-plugin/plugin.json", json.dumps({"name": "evil"})
            )
        try:
            install(evil_zip, tmp_path / "evil_root")
            raise AssertionError("zip-slip was accepted")
        except InstallError:
            pass

        index = tmp_path / "marketplace.json"
        index.write_text(
            json.dumps(
                {
                    "version": 1,
                    "plugins": [
                        {
                            "name": "myplugin",
                            "description": "test plugin",
                            "source": str(plugin_zip),
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        market = Marketplace(index)
        assert market.search("test") and market.get("myplugin")
        assert market.install("myplugin", tmp_path / "market_root").name == "myplugin"
        print("ok: plugin install + marketplace")

        adapter = ToolsetFeatureAdapter(toolset)
        assert adapter.pre_call(None, "") == ""
        assert adapter.tool_schemas(None)
        assert "hi" in adapter.dispatch("shell_execute", {"command": "printf hi"}, None)
        assert adapter.dispatch("nope", {}, None) is None
        print("ok: feature bridge")


def _check_persona() -> None:
    profiles = FileProfiles()
    assert "april" in profiles.names()
    april_profile = profiles.load("april")
    assert persona_kernel.derive(april_profile).proactivity == 67
    assert (
        abs(
            persona_kernel.similarity(
                profiles.load("april"), profiles.load("user")
            )
            - 0.9667
        )
        < 0.01
    )
    persona = resolve("april", profiles)
    assert "## Personality (OCEAN)" in persona.system_text
    assert abs(persona.temperature - 0.69) < 0.01
    assert isinstance(persona.attested_rules(), list)
    allowed, reason = persona.check("git push origin main")
    assert not allowed and "public" in reason
    allowed, _ = persona.check("git push origin main # user approved")
    assert allowed
    allowed, reason = persona.check("export OPENAI_API_KEY=sk-abcdefghijklmnop")
    assert not allowed and "secret" in reason.lower()
    print("ok: persona kernel + profiles + guards")


def _check_learning() -> None:
    store = InMemoryMemoryStore()
    store.add_memory(MemoryInput(memory_type="fact", content={"text": "seed"}))
    target = MemoryTarget(store)
    assert "seed" in target.read()["entries"]
    preview = target.edit({"entries": ["learned fact"]})
    assert "learned fact" in preview["entries"]
    assert target.validate(preview) == []
    assert target.evaluate(preview, Trace()) == 1.0
    target.commit(preview)
    assert "learned fact" in target.read()["entries"]

    model = FakeModel([ChatReply(text='{"entries": ["engine fact"]}')])
    library = InMemoryLessonStore()
    engine = Engine(MemoryTarget(store), model, "test", library=library, k=1)
    lesson = engine.run(Trace(evidence="the user said engine fact"))
    assert lesson and lesson.score == 1.0
    assert "engine fact" in target.read()["entries"]
    assert library.lessons() and library.lessons()[0].target == "memory"

    with tempfile.TemporaryDirectory() as tmp:
        device = DeviceTarget(Path(tmp) / "device.json", required_keys=("name",))
        assert device.validate(device.read())
        proposal = device.edit({"config": {"name": "esp32"}})
        assert device.validate(proposal) == []
        device.commit(proposal)
        assert device.read()["config"]["name"] == "esp32"

        context = Path(tmp) / "ctx"
        (context / "harness").mkdir(parents=True)
        (context / "harness" / "systemprompt.md").write_text("old", encoding="utf-8")
        proposer = Proposer(
            FakeModel(
                [
                    ChatReply(
                        text="",
                        tool_calls=[
                            ToolCall(
                                id="1",
                                name="write_file",
                                arguments=json.dumps(
                                    {
                                        "path": "context/harness/systemprompt.md",
                                        "content": "new",
                                    }
                                ),
                            )
                        ],
                    ),
                    ChatReply(text="done"),
                ]
            ),
            "test",
        )
        reply = proposer.call(
            "edit the harness", context, "t", harvest="context/harness"
        )
        assert reply.ok and reply.workspace
        assert (
            reply.workspace / "systemprompt.md"
        ).read_text(encoding="utf-8") == "new"

    with tempfile.TemporaryDirectory() as tmp:
        from april.learning.substrate import APRIL_SURFACE
        from april.learning.target import WorkflowTarget

        base = Path(tmp) / "base"
        base.mkdir()
        (base / "systemprompt.md").write_text("You are APRIL.", encoding="utf-8")
        (base / "guardrails.md").write_text("Rules.", encoding="utf-8")
        (base / "tool_guidance.md").write_text("Tools.", encoding="utf-8")

        from april.learning.april_spec import APRIL_SPEC
        from april.learning.substrate import APRIL_SUBSTRATE

        class FakeAdapter:
            spec = APRIL_SPEC
            substrate = APRIL_SUBSTRATE

            def tasks(self, split="all"):
                return ["demo/task"]

            def domain_of(self, task_id):
                return "demo"

            def task_prompt(self, task_id):
                return "Improve the demo harness."

            def task_files(self, task_id):
                return {}

            def base_harness(self):
                return base

            def validate_tree(self, tree):
                return None

            def execute(self, task_id, harness_dir, run_id, *, timeout=None):
                run = Path(tmp) / "runs" / run_id
                (run / "deliverables").mkdir(parents=True)
                (run / "deliverables" / "out.txt").write_text("ok")
                return run

            def delivered(self, run_dir):
                return (Path(run_dir) / "deliverables" / "out.txt").is_file()

            def trajectory(self, run_dir, *, obs_trunc=50_000):
                return "demo trajectory"

            def deliverable_files(self, run_dir):
                return {"out.txt": str(Path(run_dir) / "deliverables" / "out.txt")}

            def deliverable_text(self, run_dir):
                return "ok"

            def score(self, run_dir, task_id):
                return {"ok": True}

        target = WorkflowTarget(FakeAdapter(), surface=APRIL_SURFACE)
        assert "systemprompt.md" in target.read()["files"]
        edited = target.edit({"files": {"systemprompt.md": "New prompt."}})
        assert edited["files"]["systemprompt.md"] == "New prompt."
        assert target.validate(edited) == []
        assert target.evaluate(edited, Trace(task_id="t", run_id="r")) == 1.0
        target.commit(edited)
        assert target.read()["files"]["systemprompt.md"] == "New prompt."

        from april.learning.demo_adapter import DeterministicProposer

        workflow_engine = Engine(
            WorkflowTarget(FakeAdapter(), surface=APRIL_SURFACE),
            FakeModel([ChatReply(text="unused")]),
            "test",
            proposer=DeterministicProposer(),
            runs_root=Path(tmp) / "workflow_runs",
            k=1,
        )
        lesson = workflow_engine.run(Trace(task_id="demo/task"))
        assert lesson is not None and lesson.target == "workflow"

    try:
        DryProposer().call("x")
        raise AssertionError("dry proposer did not fail closed")
    except ProposerError:
        pass
    print("ok: learning engine + workflow target + native proposer")


def _check_body() -> None:
    fake = FakeDevices({"esp": {"temperature": 21.5}})
    assert fake.devices()[0].id == "esp"
    reading = fake.read("esp")
    assert reading.ok and reading.data["temperature"] == 21.5
    assert not fake.read("missing").ok
    try:
        Device(id="x", name="x", risk="bogus")
        raise AssertionError("invalid risk accepted")
    except ValueError:
        pass

    class TelemetryHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"temperature": 22.0, "humidity": 40}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), TelemetryHandler)
    Thread(target=server.serve_forever, daemon=True).start()
    try:
        esp = ESP32Devices(
            [
                Device(
                    id="esp32",
                    name="ESP32",
                    kind="esp32",
                    address=f"http://127.0.0.1:{server.server_port}/",
                )
            ]
        )
        reading = esp.read("esp32")
        assert reading.ok and reading.data["temperature"] == 22.0
        assert not esp.read("missing").ok
    finally:
        server.shutdown()
    print("ok: body telemetry")


def _check_runtime() -> None:
    persona = Persona(
        name="test",
        system_text="You are a test.",
        guardrails="",
        temperature=0.2,
        source="test",
    )
    with tempfile.TemporaryDirectory() as tmp:
        bus = EventBus()
        memory = InMemoryMemoryStore()
        base_model = FakeModel([ChatReply(text="hello")])
        runtime = Runtime(
            base_model,
            "test",
            memory,
            persona,
            Toolset([BuiltinSource(workspace=tmp)]),
            bus,
            FakeDevices(),
            base_identity="BASE",
        )
        assert runtime.respond("hi", "s1") == "hello"
        assert base_model.requests[0].messages[0]["content"].startswith("BASE")
        assert len(memory.history("s1")) == 2
        assert any(event.event_type == "message.done" for event in bus.get_history())

        usage_model = FakeModel(
            [
                ChatReply(
                    text="hello",
                    usage={
                        "prompt_tokens": 10,
                        "completion_tokens": 5,
                        "total_tokens": 15,
                    },
                )
            ]
        )
        usage_runtime = Runtime(
            usage_model,
            "test",
            InMemoryMemoryStore(),
            persona,
            Toolset([BuiltinSource(workspace=tmp)]),
            EventBus(),
            FakeDevices(),
        )
        usage_runtime.respond("hi", "u1")
        assert usage_runtime.usage["total_tokens"] == 15

        extraction_model = FakeModel(
            [
                ChatReply(text="hello"),
                ChatReply(
                    text='{"extractions":[{"type":"fact",'
                    '"content":{"text":"extracted fact"},'
                    '"confidence":0.9,"tags":["t"]}]}'
                ),
            ]
        )
        extraction_memory = InMemoryMemoryStore()
        extraction_runtime = Runtime(
            extraction_model,
            "test",
            extraction_memory,
            persona,
            Toolset([BuiltinSource(workspace=tmp)]),
            EventBus(),
            FakeDevices(),
            extractor=MemoryExtractor(extraction_model, "test"),
        )
        extraction_runtime.respond("remember this", "e1")
        assert extraction_memory.recall("extracted fact")
        assert extraction_memory.unprocessed("e1") == []

        model = FakeModel(
            [
                ChatReply(
                    text="",
                    tool_calls=[
                        ToolCall(
                            id="1",
                            name="write_file",
                            arguments=json.dumps(
                                {"path": "x.txt", "content": "data"}
                            ),
                        )
                    ],
                ),
                ChatReply(text="done"),
            ]
        )
        runtime = Runtime(
            model,
            "test",
            InMemoryMemoryStore(),
            persona,
            Toolset([BuiltinSource(workspace=tmp)]),
            EventBus(),
            FakeDevices(),
        )
        assert runtime.respond("write", "s2") == "done"
        assert (Path(tmp) / "x.txt").read_text(encoding="utf-8") == "data"

        model = FakeModel(
            [
                ChatReply(
                    text="",
                    tool_calls=[
                        ToolCall(
                            id="1",
                            name="shell_execute",
                            arguments=json.dumps({"command": "git push origin main"}),
                        )
                    ],
                ),
                ChatReply(text="stopped"),
            ]
        )
        runtime = Runtime(
            model,
            "test",
            InMemoryMemoryStore(),
            persona,
            Toolset([BuiltinSource(workspace=tmp)]),
            EventBus(),
            FakeDevices(),
        )
        assert runtime.respond("push", "s3") == "stopped"
        tool_messages = [
            message
            for message in model.requests[-1].messages
            if message.get("role") == "tool"
        ]
        assert tool_messages and "Blocked" in tool_messages[-1]["content"]

        for path in Path("april/runtime").glob("*.py"):
            text = path.read_text(encoding="utf-8")
            for token in ("openai", "sqlite3", "subprocess", "yaml"):
                assert f"import {token}" not in text, (path, token)
                assert f"from {token}" not in text, (path, token)

        settings = Settings(
            model="test",
            api_key="x",
            api_base_url="http://localhost",
            persona="april",
            memory_db_path=str(Path(tmp) / "mem.db"),
            workspace=tmp,
        )
        built = build(settings, memory_db_path=str(Path(tmp) / "mem.db"))
        assert built.persona.name == "april"
    print("ok: runtime turn + guard + architecture + app")


def _check_api_pure() -> None:
    from april.api.serialize import event_to_dict, session_payload

    bus = EventBus()
    event = bus.emit_message_delta("hi", is_assistant=True)
    row = event_to_dict(event)
    assert row["type"] == "message.delta"
    assert row["payload"]["content"] == "hi"
    payload = session_payload("s", [{"role": "user", "content": "x"}])
    assert payload["messages"][0]["content"] == "x"
    print("ok: api serialization")


def main() -> int:
    _check_llm()
    _check_events()
    _check_memory()
    _check_tools()
    _check_persona()
    _check_learning()
    _check_body()
    _check_runtime()
    _check_api_pure()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
