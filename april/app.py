"""The composition root: the only file that names concrete adapters."""

from __future__ import annotations

from pathlib import Path

from .body.esp32 import ESP32Devices
from .config import Settings
from .events import EventBus, FileSink
from .learning.engine import Engine
from .learning.lessons import FileLessonStore
from .learning.proposer import Proposer
from .learning.target import DeviceTarget, MemoryTarget, WorkflowTarget
from .llm.openai import OpenAIModel
from .memory.extract import MemoryExtractor
from .memory.sqlite import SQLiteMemory
from .persona.profiles import FileProfiles, resolve
from .runtime.agent import Runtime
from .tools import BuiltinSource, McpRegistry, McpSource, SkillLoader, SkillsSource, Toolset


def build(
    settings: Settings,
    *,
    workspace: str | Path | None = None,
    profiles_dir: str | Path | None = None,
    memory_db_path: str | Path | None = None,
    audit_dir: str | Path | None = None,
    start_mcp: bool = False,
    adapter=None,
) -> Runtime:
    workspace_path = Path(workspace or settings.workspace or ".")
    model = OpenAIModel(settings.api_key, settings.api_base_url)
    memory = SQLiteMemory(memory_db_path or settings.memory_db_path)
    persona = resolve(settings.persona or "april", FileProfiles(profiles_dir))

    skills = SkillsSource(SkillLoader(workspace=workspace_path))
    mcp = McpSource(McpRegistry())
    if start_mcp:
        mcp.start()
    tools = Toolset([BuiltinSource(workspace_path), skills, mcp])

    events = EventBus()
    if audit_dir:
        FileSink(Path(audit_dir) / "april.log").attach(events)

    identity_path = (
        Path(__file__).resolve().parent
        / "learning"
        / "basetree"
        / "april"
        / "systemprompt.md"
    )
    base_identity = (
        identity_path.read_text(encoding="utf-8").strip()
        if identity_path.is_file()
        else ""
    )
    extractor = MemoryExtractor(model, settings.model)
    proposer = Proposer(model, settings.model, audit_dir=audit_dir)

    lessons = FileLessonStore(Path(audit_dir or "runs") / "lessons.jsonl")
    engines = {
        "memory": Engine(
            MemoryTarget(memory), model, settings.model, library=lessons
        ),
        "device": Engine(
            DeviceTarget(
                workspace_path / ".april" / "device.json",
                required_keys=("name",),
            ),
            model,
            settings.model,
            library=lessons,
        ),
    }
    if adapter is not None:
        engines["workflow"] = Engine(
            WorkflowTarget(adapter),
            model,
            settings.model,
            library=lessons,
            proposer=proposer,
            runs_root=Path(audit_dir or "runs") / "workflow",
        )

    return Runtime(
        model,
        settings.model,
        memory,
        persona,
        tools,
        events,
        ESP32Devices(),
        engines,
        learn_targets=settings.learn_targets,
        extractor=extractor,
        base_identity=base_identity,
        pricing=settings.pricing_per_1k(),
    )
