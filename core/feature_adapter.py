"""Unified feature adapter contract and runtime registry.

Every APRIL feature (identity, memory, tools, skills, subagents, MCP, task
context) implements the same phased lifecycle:

    pre_call      -> context text inserted before the LLM call (never stored)
    tool_schemas  -> OpenAI tool declarations this feature contributes
    dispatch      -> execute a tool owned by this feature, or None
    post_call     -> side effects after the final assistant response

`FeatureRuntime` is an ordered list of adapters. Tool ownership is first-match:
the first feature whose ``dispatch()`` returns a non-None string owns the call.

Feature failures are logged and swallowed so a broken provider never blocks an
LLM call.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import re
import shutil
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger("april.features")

DEFAULT_IDENTITY = (
    "You are APRIL, an assistant with a body. You act through the machine you "
    "run on and the devices on the network: read state before acting, take the "
    "smallest reversible step, verify the effect, and report what actually "
    "happened."
)

_BASE_IDENTITY_PATH = (
    Path(__file__).resolve().parent
    / "harness"
    / "basetree"
    / "april"
    / "systemprompt.md"
)
_DEFAULT_IDENTITY_CACHE: str | None = None


def default_identity() -> str:
    """The shipped APRIL prompt, cached; a compact fallback in partial checkouts."""
    global _DEFAULT_IDENTITY_CACHE
    if _DEFAULT_IDENTITY_CACHE is None:
        try:
            text = _BASE_IDENTITY_PATH.read_text(encoding="utf-8").strip()
        except OSError:
            text = ""
        _DEFAULT_IDENTITY_CACHE = text or DEFAULT_IDENTITY
    return _DEFAULT_IDENTITY_CACHE


@runtime_checkable
class FeatureAdapter(Protocol):
    """One phased feature: context in, tools in/out, post-turn side effects."""

    name: str

    def pre_call(self, agent: Any, user_input: str) -> str:
        """Context text inserted before the LLM call ("" if none)."""

    def tool_schemas(self, agent: Any) -> list[dict]:
        """OpenAI tool schemas this feature contributes ([] if none)."""

    def dispatch(self, tool_name: str, args: dict, agent: Any) -> str | None:
        """Execute a tool owned by this feature, or None if not owned."""

    def post_call(self, agent: Any, user_input: str, assistant_response: str) -> None:
        """Post-processing after the model responds."""


class NoopFeatureAdapter:
    """Default adapter: contributes nothing, owns no tools, does nothing."""

    def __init__(self, name: str = "noop") -> None:
        self.name = name

    def pre_call(self, agent: Any, user_input: str) -> str:
        return ""

    def tool_schemas(self, agent: Any) -> list[dict]:
        return []

    def dispatch(self, tool_name: str, args: dict, agent: Any) -> str | None:
        return None

    def post_call(
        self, agent: Any, user_input: str, assistant_response: str
    ) -> None:
        return None


def _report_feature_error(
    agent: Any, feature_name: str, phase: str, exc: Exception
) -> None:
    """Log a feature failure without ever letting it block the agent loop."""
    logger.warning("feature %s failed in %s: %s", feature_name, phase, exc)
    emit = getattr(agent, "_emit_error", None)
    if callable(emit):
        try:
            emit(f"feature_{phase}", f"{feature_name}: {exc}")
        except Exception:
            pass


def _log(agent: Any, level: str, source: str, message: str) -> None:
    """Emit a log event when the agent has an event bus; never raise."""
    bus = getattr(agent, "_event_bus", None)
    emit = getattr(bus, "emit_log", None)
    if callable(emit):
        try:
            emit(level=level, source=source, message=message)
        except Exception:
            pass


class FeatureRuntime(list):
    """Ordered registry of FeatureAdapters with phased helper methods."""

    def register(self, feature: FeatureAdapter) -> FeatureAdapter:
        if not getattr(feature, "name", ""):
            raise ValueError("a feature adapter needs a non-empty name")
        self.append(feature)
        return feature

    def get(self, name: str) -> FeatureAdapter | None:
        for feature in self:
            if getattr(feature, "name", "") == name:
                return feature
        return None

    # -- pre-call ------------------------------------------------------------

    def pre_call_sections(self, agent: Any, user_input: str) -> list[tuple[str, str]]:
        """Run every feature's pre_call, returning (name, text) pairs."""
        sections: list[tuple[str, str]] = []
        for feature in list(self):
            name = str(getattr(feature, "name", feature.__class__.__name__))
            try:
                text = feature.pre_call(agent, user_input)
            except Exception as exc:
                _report_feature_error(agent, name, "pre_call", exc)
                continue
            if text:
                sections.append((name, str(text)))
        return sections

    def pre_call(self, agent: Any, user_input: str) -> str:
        """All pre-call text joined into one block."""
        return "\n\n".join(
            text for _name, text in self.pre_call_sections(agent, user_input)
        )

    def identity_and_context(self, agent: Any, user_input: str) -> tuple[str, str]:
        """Split pre-call output into the identity head and runtime context.

        Identity is its own system message (so it frames every request); every
        other feature's text is joined into the runtime-context system message.
        """
        identity_parts: list[str] = []
        context_parts: list[str] = []
        for name, text in self.pre_call_sections(agent, user_input):
            if name == "identity":
                identity_parts.append(text)
            else:
                context_parts.append(text)
        return "\n\n".join(identity_parts), "\n\n".join(context_parts)

    # -- tools ---------------------------------------------------------------

    def tool_schemas(self, agent: Any) -> list[dict]:
        """Merge tool schemas; the first declaration of a name wins."""
        schemas: list[dict] = []
        seen: set[str] = set()
        for feature in list(self):
            name = str(getattr(feature, "name", feature.__class__.__name__))
            try:
                contributed = feature.tool_schemas(agent) or []
            except Exception as exc:
                _report_feature_error(agent, name, "tool_schemas", exc)
                continue
            for schema in contributed:
                if not isinstance(schema, dict):
                    continue
                function = schema.get("function")
                tool_name = (
                    function.get("name")
                    if isinstance(function, dict)
                    else schema.get("name")
                )
                if tool_name:
                    if tool_name in seen:
                        continue
                    seen.add(tool_name)
                schemas.append(schema)
        return schemas

    def dispatch(self, tool_name: str, args: dict, agent: Any) -> str | None:
        """First ownership wins: run the first adapter that claims the tool."""
        for feature in list(self):
            name = str(getattr(feature, "name", feature.__class__.__name__))
            try:
                result = feature.dispatch(tool_name, args, agent)
            except Exception as exc:
                _report_feature_error(agent, name, "dispatch", exc)
                return f"Error executing tool '{tool_name}': {exc}"
            if result is not None:
                return result if isinstance(result, str) else str(result)
        return None

    # -- post-call -----------------------------------------------------------

    def post_call(
        self, agent: Any, user_input: str, assistant_response: str
    ) -> None:
        for feature in list(self):
            name = str(getattr(feature, "name", feature.__class__.__name__))
            try:
                feature.post_call(agent, user_input, assistant_response)
            except Exception as exc:
                _report_feature_error(agent, name, "post_call", exc)


class IdentityFeatureAdapter:
    """The APRIL/persona prompt, rendered as the first system message."""

    name = "identity"

    def __init__(self, prompt: str | None = None) -> None:
        self.prompt = prompt

    def pre_call(self, agent: Any, user_input: str) -> str:
        boot = getattr(agent, "persona_boot", None)
        if boot is not None:
            text = str(getattr(boot, "system_prompt", "") or "").strip()
            if text:
                return text

        override = self.prompt or getattr(agent, "identity_prompt", None)
        if override:
            return str(override).strip()
        return default_identity()

    def tool_schemas(self, agent: Any) -> list[dict]:
        return []

    def dispatch(self, tool_name: str, args: dict, agent: Any) -> str | None:
        return None

    def post_call(
        self, agent: Any, user_input: str, assistant_response: str
    ) -> None:
        return None


class MemoryFeatureAdapter:
    """Retrieve top-k memories before the call; extract/store after the turn."""

    name = "memory"

    def pre_call(self, agent: Any, user_input: str) -> str:
        db = getattr(agent, "db", None)
        session = getattr(agent, "session", None)
        if db is None or session is None or not user_input:
            return ""

        config = getattr(agent, "config", None)
        limit = int(getattr(config, "memory_max_context", 10) or 10)
        try:
            memories = session.get_relevant_memories(user_input, limit=limit)
        except Exception as exc:
            _report_feature_error(agent, self.name, "pre_call", exc)
            return ""

        if not memories:
            return ""

        lines = ["Relevant previous knowledge from past conversations:"]
        for mem in memories:
            if not isinstance(mem, dict):
                continue
            mem_type = str(mem.get("memory_type") or "memory").upper()
            content = mem.get("content") or {}
            text = (
                content.get("text", "")
                if isinstance(content, dict)
                else str(content)
            )
            if text:
                lines.append(f"- [{mem_type}] {text}")
        return "\n".join(lines) if len(lines) > 1 else ""

    def tool_schemas(self, agent: Any) -> list[dict]:
        return []

    def dispatch(self, tool_name: str, args: dict, agent: Any) -> str | None:
        return None

    def post_call(
        self, agent: Any, user_input: str, assistant_response: str
    ) -> None:
        # No assistant response means nothing new to extract (matches the old
        # `if final_response:` guard).
        if not assistant_response:
            return
        self.extract(agent)

    def extract(self, agent: Any) -> None:
        """Read unprocessed turns from the store and persist extracted memories."""
        db = getattr(agent, "db", None)
        extractor = getattr(agent, "extractor", None)
        session = getattr(agent, "session", None)
        if db is None or extractor is None or session is None:
            return
        if not getattr(session, "id", ""):
            return

        try:
            messages = db.get_unprocessed_messages(session.id)
            if not messages:
                return

            _log(
                agent,
                "DEBUG",
                "memory",
                f"Extracting memories from {len(messages)} messages",
            )
            memories = extractor.extract(
                messages, client=getattr(agent, "client", None)
            )
            _log(
                agent,
                "DEBUG",
                "memory",
                f"Extracted {len(memories)} memories",
            )

            for memory in memories:
                db.save_memory(
                    session_id=session.id,
                    memory_type=memory["type"],
                    content=memory["content"],
                    confidence=memory["confidence"],
                    tags=memory["tags"],
                    source_message_ids=[m["id"] for m in messages],
                )

            message_ids = [m["id"] for m in messages]
            db.mark_processed(message_ids)
            _log(
                agent,
                "DEBUG",
                "memory",
                f"Marked {len(message_ids)} messages as processed",
            )
        except Exception as exc:
            _report_feature_error(agent, self.name, "post_call", exc)


class ToolFeatureAdapter:
    """Adapter for one tool object implementing ``name``/``schema()``/``execute()``."""

    def __init__(self, tool: Any) -> None:
        self.tool = tool
        self.tool_name = str(getattr(tool, "name", "") or "")
        self.name = f"tool:{self.tool_name or 'unknown'}"

    def pre_call(self, agent: Any, user_input: str) -> str:
        return ""

    def tool_schemas(self, agent: Any) -> list[dict]:
        schema = getattr(self.tool, "schema", None)
        if not callable(schema):
            return []
        value = schema()
        return [value] if isinstance(value, dict) else []

    def dispatch(self, tool_name: str, args: dict, agent: Any) -> str | None:
        if tool_name != self.tool_name:
            return None
        return self.execute(dict(args or {}))

    def execute(self, args: dict) -> Any:
        return self.tool.execute(**args)

    def post_call(
        self, agent: Any, user_input: str, assistant_response: str
    ) -> None:
        return None


class ShellFeatureAdapter(ToolFeatureAdapter):
    """Shell proof-of-concept: schema + dispatch through the unified lifecycle."""

    def __init__(self, tool: Any) -> None:
        super().__init__(tool)
        self.name = "shell"

    def execute(self, args: dict) -> Any:
        return self.tool.execute(
            command=args.get("command"),
            timeout=args.get("timeout", 30),
        )


class ToolSummaryFeatureAdapter:
    """Pre-call summary of the tool registry (schemas themselves are per-tool)."""

    name = "tools"

    def __init__(self, tools: list[Any]) -> None:
        self.tools = list(tools)

    def pre_call(self, agent: Any, user_input: str) -> str:
        lines: list[str] = []
        for tool in self.tools:
            name = str(getattr(tool, "name", "") or "")
            if not name:
                continue
            description = str(getattr(tool, "description", "") or "").strip()
            if not description:
                schema = getattr(tool, "schema", None)
                if callable(schema):
                    try:
                        spec = schema()
                        function = (
                            spec.get("function")
                            if isinstance(spec, dict)
                            else None
                        )
                        if isinstance(function, dict):
                            description = str(
                                function.get("description", "")
                            ).strip()
                    except Exception:
                        description = ""
            lines.append(f"- `{name}`: {description}" if description else f"- `{name}`")
        if not lines:
            return ""
        return "Tool families available:\n" + "\n".join(lines)

    def tool_schemas(self, agent: Any) -> list[dict]:
        return []

    def dispatch(self, tool_name: str, args: dict, agent: Any) -> str | None:
        return None

    def post_call(
        self, agent: Any, user_input: str, assistant_response: str
    ) -> None:
        return None


class _StubFeature:
    """Named placeholder for a feature whose runtime lands in a later phase."""

    name = "stub"

    def pre_call(self, agent: Any, user_input: str) -> str:
        return ""

    def tool_schemas(self, agent: Any) -> list[dict]:
        return []

    def dispatch(self, tool_name: str, args: dict, agent: Any) -> str | None:
        return None

    def post_call(
        self, agent: Any, user_input: str, assistant_response: str
    ) -> None:
        return None


_SKILL_NAME_RX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _workspace_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _function_schema(
    name: str,
    description: str,
    properties: dict,
    required: list[str] | None = None,
) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required or [],
            },
        },
    }


def _parse_skill_text(text: str) -> tuple[dict[str, str], str]:
    """Parse the simple SKILL.md frontmatter subset (scalars + > / | blocks)."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end < 0:
        return {}, text
    block = text[3:end].strip("\r\n")
    body = text[end + 4 :]
    if body.startswith("\r\n"):
        body = body[2:]
    elif body.startswith("\n"):
        body = body[1:]

    data: dict[str, str] = {}
    lines = block.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        if not line.strip() or line.lstrip().startswith("#") or line[:1].isspace():
            continue
        key, separator, raw_value = line.partition(":")
        if not separator:
            continue
        key = key.strip()
        value = raw_value.strip()
        if value in (">", "|", ">-", "|-", ">+", "|+"):
            folded = value.startswith(">")
            collected: list[str] = []
            while index < len(lines):
                nested = lines[index]
                if nested[:1].isspace() or not nested.strip():
                    collected.append(nested.strip())
                    index += 1
                else:
                    break
            data[key] = (
                " ".join(part for part in collected if part)
                if folded
                else "\n".join(collected)
            ).strip()
        else:
            data[key] = value.strip().strip("'\"")
    return data, body


def _skill_document(name: str, description: str, body: str) -> str:
    if "\n" in description:
        rendered = "|\n" + "\n".join(f"  {line}" for line in description.splitlines())
    else:
        rendered = description
    return (
        f"---\nname: {name}\ndescription: {rendered}\n---\n\n"
        f"{body.rstrip()}\n"
    )


@dataclass
class SkillRecord:
    name: str
    description: str
    path: Path
    scope: str
    text: str
    body: str


def _default_external_roots(workspace: Path) -> list[tuple[str, Path]]:
    """Local skill directories of other agents that can be imported."""
    home = Path.home()
    specs = [
        ("claude", home / ".claude" / "skills"),
        ("claude-workspace", workspace / ".claude" / "skills"),
        ("codex", home / ".codex" / "skills"),
        ("codex-workspace", workspace / ".codex" / "skills"),
        ("opencode", home / ".config" / "opencode" / "skills"),
        ("opencode-workspace", workspace / ".opencode" / "skills"),
        ("openclaw", home / ".openclaw" / "skills"),
        ("augment", home / ".augment" / "skills"),
        ("augment-workspace", workspace / ".augment" / "skills"),
        ("continue", home / ".continue" / "skills"),
        ("continue-workspace", workspace / ".continue" / "skills"),
        ("goose", home / ".config" / "goose" / "skills"),
        ("goose-workspace", workspace / ".goose" / "skills"),
        ("qwen", home / ".qwen" / "skills"),
        ("qwen-workspace", workspace / ".qwen" / "skills"),
        ("qoder", home / ".qoder" / "skills"),
        ("qoder-workspace", workspace / ".qoder" / "skills"),
        ("windsurf", home / ".codeium" / "windsurf" / "skills"),
        ("windsurf-workspace", workspace / ".windsurf" / "skills"),
        ("trae", home / ".trae" / "skills"),
        ("trae-workspace", workspace / ".trae" / "skills"),
        ("kiro", home / ".kiro" / "skills"),
        ("kiro-workspace", workspace / ".kiro" / "skills"),
        ("roo", home / ".roo" / "skills"),
        ("roo-workspace", workspace / ".roo" / "skills"),
        ("codebuddy", home / ".codebuddy" / "skills"),
        ("codebuddy-workspace", workspace / ".codebuddy" / "skills"),
    ]
    seen: set[str] = set()
    roots: list[tuple[str, Path]] = []
    for label, path in specs:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        roots.append((label, path))
    return roots


class SkillsFeatureAdapter(_StubFeature):
    """Skill catalog, on-demand loading, CRUD, and local-agent import."""

    name = "skills"
    _WRITE_TOOLS = {"create_skill", "update_skill", "delete_skill", "import_skill"}

    def __init__(
        self,
        workspace: str | Path | None = None,
        user_root: str | Path | None = None,
        external_roots: list[tuple[str, str | Path]] | None = None,
        allow_writes: bool | None = None,
    ) -> None:
        self.workspace = (
            Path(workspace) if workspace is not None else _workspace_root()
        )
        self.user_root = (
            Path(user_root)
            if user_root is not None
            else Path.home() / ".agents" / "skills"
        )
        if external_roots is None:
            self.external_roots = _default_external_roots(self.workspace)
        else:
            self.external_roots = [
                (str(label), Path(path)) for label, path in external_roots
            ]
        if allow_writes is None:
            allow_writes = os.getenv("APRIL_SKILL_WRITE", "1").strip() != "0"
        self.allow_writes = bool(allow_writes)

    # -- discovery -----------------------------------------------------------

    def _scope_roots(self, scope: str) -> list[Path]:
        if scope == "workspace":
            return [self.workspace / ".agents" / "skills", self.workspace / "skills"]
        if scope == "user":
            return [self.user_root]
        raise ValueError(f"unknown skill scope: {scope!r}")

    def _read_record(self, skill_md: Path, scope: str) -> SkillRecord | None:
        try:
            text = skill_md.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        front, body = _parse_skill_text(text)
        name = str(front.get("name") or skill_md.parent.name).strip()
        if not name:
            return None
        return SkillRecord(
            name=name,
            description=str(front.get("description") or "").strip(),
            path=skill_md,
            scope=scope,
            text=text,
            body=body,
        )

    def _scan_scope(self, scope: str) -> dict[str, SkillRecord]:
        records: dict[str, SkillRecord] = {}
        for root in self._scope_roots(scope):
            if not root.is_dir():
                continue
            for skill_md in sorted(root.glob("*/SKILL.md")):
                record = self._read_record(skill_md, scope)
                if record is not None:
                    records.setdefault(record.name, record)
        return records

    def _discover(self) -> dict[str, SkillRecord]:
        records = self._scan_scope("user")
        records.update(self._scan_scope("workspace"))
        return records

    def _find(self, name: str, scope: str | None = None) -> SkillRecord | None:
        clean = str(name or "").strip()
        if scope in ("workspace", "user"):
            for record in self._scan_scope(scope).values():
                if record.name == clean:
                    return record
            return None
        return self._discover().get(clean)

    def _target_root(self, scope: str) -> Path:
        if scope == "user":
            return self.user_root
        if scope == "workspace":
            return self.workspace / ".agents" / "skills"
        raise ValueError(f"unknown skill scope: {scope!r}")

    @staticmethod
    def _validate_name(name: str) -> str | None:
        if not name or not _SKILL_NAME_RX.fullmatch(name):
            return "skill names must match [A-Za-z0-9][A-Za-z0-9._-]*"
        if ".." in name:
            return "skill names cannot contain '..'"
        return None

    @staticmethod
    def _confirmed(args: dict) -> bool:
        value = args.get("confirm")
        return value is True or str(value or "").strip().lower() == "true"

    def _confirmation(self, action: str, args: dict) -> str | None:
        if self._confirmed(args):
            return None
        return (
            f"CONFIRMATION REQUIRED: {action}. Ask the user to approve this "
            "change explicitly, then call the tool again with confirm=true."
        )

    # -- LLM surface ---------------------------------------------------------

    def pre_call(self, agent: Any, user_input: str) -> str:
        records = self._discover()
        if not records:
            return ""
        lines = [
            "Skills available — call `load_skill` with the skill name when the "
            "user's request matches its description or the user names it:"
        ]
        for record in sorted(records.values(), key=lambda item: item.name.lower()):
            description = record.description or "(no description)"
            lines.append(f"- `{record.name}` [{record.scope}]: {description}")
        return "\n".join(lines)

    def tool_schemas(self, agent: Any) -> list[dict]:
        schemas = [
            _function_schema(
                "load_skill",
                "Read a skill's full SKILL.md instructions. Use only when the "
                "user's request matches the skill's description or the user "
                "names the skill.",
                {
                    "name": {
                        "type": "string",
                        "description": "Skill directory name (see the catalog).",
                    }
                },
                ["name"],
            ),
            _function_schema(
                "list_external_skills",
                "List skills installed for other local agents (Claude, Codex, "
                "OpenCode, OpenClaw, and similar) that can be imported.",
                {
                    "source": {
                        "type": "string",
                        "description": "Optional source label or path to filter.",
                    }
                },
            ),
        ]
        if not self.allow_writes:
            return schemas
        scope = {
            "type": "string",
            "enum": ["workspace", "user"],
            "description": "Where the skill lives (default: workspace).",
        }
        confirm = {
            "type": "boolean",
            "description": "Set true only after the user explicitly approves.",
        }
        schemas.extend(
            [
                _function_schema(
                    "create_skill",
                    "Create a new workspace/user skill. Destructive: requires "
                    "explicit user confirmation.",
                    {
                        "name": {"type": "string"},
                        "description": {
                            "type": "string",
                            "description": "When the skill should be used.",
                        },
                        "body": {
                            "type": "string",
                            "description": "Markdown instructions after frontmatter.",
                        },
                        "scope": scope,
                        "confirm": confirm,
                    },
                    ["name", "description", "body"],
                ),
                _function_schema(
                    "update_skill",
                    "Replace a skill's description and/or body. Destructive: "
                    "requires explicit user confirmation.",
                    {
                        "name": {"type": "string"},
                        "description": {"type": "string"},
                        "body": {"type": "string"},
                        "scope": scope,
                        "confirm": confirm,
                    },
                    ["name"],
                ),
                _function_schema(
                    "delete_skill",
                    "Delete a skill directory. Destructive: requires explicit "
                    "user confirmation.",
                    {
                        "name": {"type": "string"},
                        "scope": scope,
                        "confirm": confirm,
                    },
                    ["name"],
                ),
                _function_schema(
                    "import_skill",
                    "Copy a skill from another local agent's skills directory "
                    "into this workspace/user. Destructive: creates files and "
                    "requires explicit user confirmation.",
                    {
                        "name": {"type": "string"},
                        "source": {
                            "type": "string",
                            "description": "Source label or path from "
                            "list_external_skills.",
                        },
                        "scope": scope,
                        "confirm": confirm,
                    },
                    ["name"],
                ),
            ]
        )
        return schemas

    def dispatch(self, tool_name: str, args: dict, agent: Any) -> str | None:
        arguments = dict(args or {})
        if tool_name == "load_skill":
            return self._load(str(arguments.get("name") or ""))
        if tool_name == "list_external_skills":
            return self._list_external(str(arguments.get("source") or ""))
        if tool_name not in self._WRITE_TOOLS:
            return None
        if not self.allow_writes:
            return "Error: skill writes are disabled (APRIL_SKILL_WRITE=0)."
        if tool_name == "create_skill":
            return self._create(arguments)
        if tool_name == "update_skill":
            return self._update(arguments)
        if tool_name == "delete_skill":
            return self._delete(arguments)
        return self._import(arguments)

    def post_call(
        self, agent: Any, user_input: str, assistant_response: str
    ) -> None:
        return None

    # -- tool implementations ------------------------------------------------

    def _load(self, name: str) -> str:
        record = self._find(name)
        if record is None:
            available = ", ".join(sorted(self._discover())) or "none"
            return f"Error: unknown skill '{name}'. Available skills: {available}."
        resources: list[str] = []
        try:
            for path in sorted(record.path.parent.rglob("*")):
                if path.is_file() and path != record.path:
                    resources.append(str(path.relative_to(record.path.parent)))
                    if len(resources) >= 50:
                        break
        except OSError:
            pass
        lines = [
            f"# Skill: {record.name}",
            f"Scope: {record.scope}",
            f"Source: {record.path}",
            "",
            record.text.strip(),
        ]
        if resources:
            lines.extend(["", "Resources:", *[f"- {item}" for item in resources]])
        return "\n".join(lines)

    def _create(self, args: dict) -> str:
        name = str(args.get("name") or "").strip()
        description = str(args.get("description") or "").strip()
        body = str(args.get("body") or "").strip()
        scope = str(args.get("scope") or "workspace").strip().lower()
        problem = self._validate_name(name)
        if problem:
            return f"Error: {problem}."
        if not description:
            return "Error: description is required."
        if not body:
            return "Error: body is required."
        warning = self._confirmation(f"create skill '{name}' in {scope} scope", args)
        if warning:
            return warning
        try:
            target_root = self._target_root(scope)
        except ValueError as exc:
            return f"Error: {exc}."
        target = target_root / name
        if target.exists():
            return f"Error: skill '{name}' already exists at {target}."
        try:
            target.mkdir(parents=True, exist_ok=False)
            (target / "SKILL.md").write_text(
                _skill_document(name, description, body), encoding="utf-8"
            )
        except OSError as exc:
            return f"Error creating skill: {exc}"
        return f"Created skill '{name}' ({scope}) at {target / 'SKILL.md'}."

    def _update(self, args: dict) -> str:
        name = str(args.get("name") or "").strip()
        scope = str(args.get("scope") or "").strip().lower() or None
        record = self._find(name, scope)
        if record is None:
            return f"Error: unknown skill '{name}'."
        warning = self._confirmation(f"update skill '{name}'", args)
        if warning:
            return warning
        description = args.get("description")
        description = (
            record.description if description is None else str(description).strip()
        )
        body = args.get("body")
        body = record.body if body is None else str(body).strip()
        try:
            record.path.write_text(
                _skill_document(record.name, description, body), encoding="utf-8"
            )
        except OSError as exc:
            return f"Error updating skill: {exc}"
        return f"Updated skill '{record.name}' at {record.path}."

    def _delete(self, args: dict) -> str:
        name = str(args.get("name") or "").strip()
        scope = str(args.get("scope") or "").strip().lower() or None
        record = self._find(name, scope)
        if record is None:
            return f"Error: unknown skill '{name}'."
        warning = self._confirmation(f"delete skill '{name}'", args)
        if warning:
            return warning
        directory = record.path.parent.resolve()
        try:
            root = self._target_root(record.scope).resolve()
            directory.relative_to(root)
        except (ValueError, OSError):
            return "Error: refusing to delete a skill outside its scope root."
        try:
            shutil.rmtree(directory)
        except OSError as exc:
            return f"Error deleting skill: {exc}"
        return f"Deleted skill '{record.name}' ({record.scope})."

    def _list_external(self, source: str) -> str:
        items: list[dict[str, str]] = []
        for label, root in self.external_roots:
            if source and source not in (label, str(root)):
                continue
            if not root.is_dir():
                continue
            for skill_md in sorted(root.glob("*/SKILL.md")):
                record = self._read_record(skill_md, "external")
                if record is None:
                    continue
                items.append(
                    {
                        "source": label,
                        "name": record.name,
                        "description": record.description,
                        "path": str(skill_md.parent),
                    }
                )
        if not items:
            labels = ", ".join(label for label, _ in self.external_roots)
            wanted = f" for {source!r}" if source else ""
            return f"No external skills found{wanted}. Sources: {labels}."
        return json.dumps(items, indent=2)

    def _import(self, args: dict) -> str:
        name = str(args.get("name") or "").strip()
        source = str(args.get("source") or "").strip()
        scope = str(args.get("scope") or "workspace").strip().lower()
        candidates = self._external_candidates(name, source)
        if not candidates:
            wanted = f" in source {source!r}" if source else ""
            return f"Error: no external skill named '{name}' found{wanted}."
        if len(candidates) > 1:
            listing = ", ".join(
                f"{label}:{skill_dir}" for label, skill_dir, _record in candidates
            )
            return (
                f"Error: skill '{name}' exists in multiple sources ({listing}); "
                "pass source=<label or path>."
            )
        warning = self._confirmation(
            f"import skill '{name}' into {scope} scope", args
        )
        if warning:
            return warning
        label, skill_dir, record = candidates[0]
        try:
            target_root = self._target_root(scope)
        except ValueError as exc:
            return f"Error: {exc}."
        target = target_root / record.name
        if target.exists():
            return f"Error: skill '{record.name}' already exists at {target}."
        try:
            target_root.mkdir(parents=True, exist_ok=True)
            shutil.copytree(skill_dir, target)
        except OSError as exc:
            return f"Error importing skill: {exc}"
        return f"Imported skill '{record.name}' from {label} into {target}."

    def _external_candidates(
        self, name: str, source: str
    ) -> list[tuple[str, Path, SkillRecord]]:
        candidates: list[tuple[str, Path, SkillRecord]] = []
        for label, root in self.external_roots:
            if not root.is_dir():
                continue
            skill_md = root / name / "SKILL.md"
            if not skill_md.is_file():
                continue
            if source and source not in (
                label,
                str(root),
                str(skill_md.parent),
            ):
                continue
            record = self._read_record(skill_md, "external")
            if record is not None:
                candidates.append((label, skill_md.parent, record))
        return candidates


class SubagentFeatureAdapter(_StubFeature):
    """Run one self-contained task in an isolated child runtime (depth 1)."""

    name = "subagents"

    def __init__(self, manager_factory: Any = None) -> None:
        self._manager = None
        self._manager_factory = manager_factory
        self._lock = threading.RLock()

    @staticmethod
    def _depth(agent: Any) -> int:
        config = getattr(agent, "config", None)
        try:
            return int(getattr(config, "_subagent_depth", 0) or 0)
        except (TypeError, ValueError):
            return 0

    def _manager_for(self, agent: Any) -> Any:
        with self._lock:
            if self._manager is None:
                if self._manager_factory is not None:
                    self._manager = self._manager_factory(agent)
                else:
                    from .subagent import SubagentManager

                    child_config = copy.copy(agent.config)
                    child_config._subagent_depth = self._depth(agent) + 1
                    self._manager = SubagentManager(child_config)
            return self._manager

    def pre_call(self, agent: Any, user_input: str) -> str:
        if self._depth(agent) >= 1:
            return ""
        return (
            "Subagents: `run_subagent` runs one self-contained task in an "
            "isolated worker and returns its result; `list_subagents` shows "
            "live statuses. Subagents cannot spawn further subagents."
        )

    def tool_schemas(self, agent: Any) -> list[dict]:
        if self._depth(agent) >= 1:
            return []
        return [
            _function_schema(
                "run_subagent",
                "Run a self-contained task in an isolated subagent and wait "
                "for its result. Use for work that can run without the parent "
                "conversation's context.",
                {
                    "description": {
                        "type": "string",
                        "description": "Complete, self-contained task text.",
                    },
                    "role": {
                        "type": "string",
                        "description": "Worker role label (default: general).",
                    },
                    "timeout": {
                        "type": "number",
                        "description": "Seconds to wait (default: 300).",
                    },
                    "max_iterations": {
                        "type": "integer",
                        "description": "Tool-call budget for the child (default: 50).",
                    },
                },
                ["description"],
            ),
            _function_schema(
                "list_subagents",
                "List subagents spawned in this session and their statuses.",
                {},
            ),
        ]

    def dispatch(self, tool_name: str, args: dict, agent: Any) -> str | None:
        if self._depth(agent) >= 1:
            return None
        if tool_name == "run_subagent":
            return self._run(dict(args or {}), agent)
        if tool_name == "list_subagents":
            return self._list()
        return None

    def post_call(
        self, agent: Any, user_input: str, assistant_response: str
    ) -> None:
        return None

    def close(self) -> None:
        with self._lock:
            if self._manager is not None:
                try:
                    self._manager.shutdown(wait=False)
                except Exception:
                    pass
                self._manager = None

    def _run(self, args: dict, agent: Any) -> str:
        description = str(args.get("description") or "").strip()
        if not description:
            return "Error: description is required."
        role = str(args.get("role") or "general").strip() or "general"
        try:
            timeout = float(args.get("timeout") or 300)
        except (TypeError, ValueError):
            timeout = 300.0
        try:
            max_iterations = int(args.get("max_iterations") or 50)
        except (TypeError, ValueError):
            max_iterations = 50
        max_iterations = max(1, min(max_iterations, 500))

        from .subagent import SubagentConfig, Task

        manager = self._manager_for(agent)
        process = manager.spawn(
            SubagentConfig(
                name=f"{role}-{uuid.uuid4().hex[:6]}",
                role=role,
                parent_id=str(getattr(agent, "subagent_id", "") or ""),
                max_iterations=max_iterations,
            )
        )
        task = Task(
            type="subagent",
            description=description,
            parameters={"role": role},
        )
        try:
            result = manager.dispatch_and_wait(process.id, task, timeout=timeout)
        except TimeoutError:
            return (
                f"Subagent {process.id} did not finish within {timeout:.0f}s; "
                "it may still be running (call list_subagents)."
            )
        except Exception as exc:
            return f"Error running subagent: {exc}"
        if result.success:
            return str(result.data or "")
        return f"Subagent {process.id} failed: {result.error or 'unknown error'}"

    def _list(self) -> str:
        if self._manager is None:
            return "No subagents have run in this session."
        return json.dumps(self._manager.list_subagents(), indent=2)


class McpFeatureAdapter(_StubFeature):
    """Connect enabled MCP servers and expose their discovered tools."""

    name = "mcp"

    def __init__(self, registry: Any = None, runtime: Any = None) -> None:
        if runtime is None:
            from .mcp_client import McpRuntime

            runtime = McpRuntime(registry=registry)
        self.runtime = runtime
        self._started = False
        self._closed = False
        self._lock = threading.RLock()

    @staticmethod
    def _enabled(agent: Any) -> bool:
        config = getattr(agent, "config", None)
        try:
            depth = int(getattr(config, "_subagent_depth", 0) or 0)
        except (TypeError, ValueError):
            depth = 0
        if depth >= 1:
            return False
        return os.getenv("APRIL_MCP_DISABLE", "0").strip() != "1"

    def start(self, agent: Any = None) -> None:
        if self._closed:
            return
        if agent is not None and not self._enabled(agent):
            return
        with self._lock:
            if self._started:
                return
            self._started = True
            try:
                self.runtime.start()
            except Exception as exc:
                _report_feature_error(agent, self.name, "start", exc)

    def refresh(self, agent: Any = None) -> None:
        if agent is not None and not self._enabled(agent):
            return
        with self._lock:
            self._closed = False
            self._started = True
            self.runtime.refresh()

    def pre_call(self, agent: Any, user_input: str) -> str:
        if not self._enabled(agent):
            return ""
        self.start(agent)
        status = self.runtime.status()
        servers = status.get("servers") or []
        if not servers:
            error = str(status.get("error") or "").strip()
            return f"MCP: {error}" if error else ""
        lines = ["MCP servers:"]
        for server in servers:
            name = server.get("name") or server.get("id")
            state = str(server.get("status") or "unknown")
            if state == "connected":
                count = len(server.get("tools") or [])
                lines.append(f"- {name}: connected ({count} tools)")
            elif state == "disabled":
                lines.append(f"- {name}: disabled")
            else:
                detail = str(server.get("error") or "").strip()
                lines.append(f"- {name}: {state}" + (f" — {detail}" if detail else ""))
        return "\n".join(lines)

    def tool_schemas(self, agent: Any) -> list[dict]:
        if not self._enabled(agent):
            return []
        self.start(agent)
        return self.runtime.tool_schemas()

    def dispatch(self, tool_name: str, args: dict, agent: Any) -> str | None:
        if not self._enabled(agent):
            return None
        self.start(agent)
        return self.runtime.dispatch(tool_name, dict(args or {}))

    def status(self, agent: Any = None) -> dict:
        if agent is not None and not self._enabled(agent):
            return {
                "error": "MCP is disabled for subagent runtimes",
                "servers": [],
                "tools": [],
            }
        self.start(agent)
        return self.runtime.status()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            try:
                self.runtime.close()
            except Exception:
                pass
            self._started = False


class TaskContextFeatureAdapter(_StubFeature):
    """Pinned task text supplied by the active turn, if any."""

    name = "task_context"

    def pre_call(self, agent: Any, user_input: str) -> str:
        return str(getattr(agent, "task_context", "") or "").strip()


def feature_for_tool(tool: Any) -> FeatureAdapter:
    """Map a registered tool object to its feature adapter."""
    if str(getattr(tool, "name", "") or "") == "shell_execute":
        return ShellFeatureAdapter(tool)
    return ToolFeatureAdapter(tool)
