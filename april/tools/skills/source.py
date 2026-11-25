"""The skills tool surface: load, create, update, delete, import, list external."""

from __future__ import annotations

from ..base import ToolResult, ToolSchema
from .loader import SkillLoader


class SkillsSource:
    name = "skills"

    def __init__(
        self,
        loader: SkillLoader,
        active_modules: set[str] | frozenset[str] = frozenset(),
    ):
        self.loader = loader
        self.active_modules = set(active_modules)

    def schemas(self) -> list[ToolSchema]:
        schemas = [
            ToolSchema(
                name="load_skill",
                description=(
                    "Read a skill's full SKILL.md instructions. Use only when the "
                    "request matches the skill's description or the user names it."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Skill name."}
                    },
                    "required": ["name"],
                },
            )
        ]
        if not self.loader.allow_writes:
            return schemas
        schemas.extend(
            [
                ToolSchema(
                    name="create_skill",
                    description="Create a SKILL.md in user or module scope. Requires confirm=true.",
                    parameters={
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "description": {"type": "string"},
                            "body": {"type": "string"},
                            "scope": {
                                "type": "string",
                                "description": "user or module:<name>",
                            },
                            "confirm": {"type": "boolean"},
                        },
                        "required": ["name", "description", "body"],
                    },
                ),
                ToolSchema(
                    name="update_skill",
                    description="Update a skill's description or body. Requires confirm=true.",
                    parameters={
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "description": {"type": "string"},
                            "body": {"type": "string"},
                            "scope": {"type": "string"},
                            "confirm": {"type": "boolean"},
                        },
                        "required": ["name"],
                    },
                ),
                ToolSchema(
                    name="delete_skill",
                    description="Delete a skill directory. Requires confirm=true.",
                    parameters={
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "scope": {"type": "string"},
                            "confirm": {"type": "boolean"},
                        },
                        "required": ["name"],
                    },
                ),
                ToolSchema(
                    name="import_skill",
                    description=(
                        "Copy an external skill into user or module scope. "
                        "Requires confirm=true."
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "source": {"type": "string"},
                            "scope": {"type": "string"},
                            "confirm": {"type": "boolean"},
                        },
                        "required": ["name"],
                    },
                ),
                ToolSchema(
                    name="list_external_skills",
                    description="List skills available to import from other agents.",
                    parameters={
                        "type": "object",
                        "properties": {"source": {"type": "string"}},
                    },
                ),
            ]
        )
        return schemas

    def dispatch(self, name: str, args: dict) -> ToolResult | None:
        if name == "load_skill":
            skill = self.loader.load(str(args.get("name") or ""), self.active_modules)
            if skill is None:
                return ToolResult(False, f"Error: unknown skill {args.get('name')!r}")
            return ToolResult(True, skill.text)
        if name == "create_skill":
            return _result(
                self.loader.create(
                    name=str(args.get("name") or ""),
                    description=str(args.get("description") or ""),
                    body=str(args.get("body") or ""),
                    scope=str(args.get("scope") or "user"),
                    confirm=_confirmed(args),
                )
            )
        if name == "update_skill":
            return _result(
                self.loader.update(
                    name=str(args.get("name") or ""),
                    description=args.get("description"),
                    body=args.get("body"),
                    scope=str(args.get("scope") or ""),
                    confirm=_confirmed(args),
                )
            )
        if name == "delete_skill":
            return _result(
                self.loader.delete(
                    name=str(args.get("name") or ""),
                    scope=str(args.get("scope") or ""),
                    confirm=_confirmed(args),
                )
            )
        if name == "import_skill":
            return _result(
                self.loader.import_skill(
                    name=str(args.get("name") or ""),
                    source=str(args.get("source") or ""),
                    scope=str(args.get("scope") or "user"),
                    confirm=_confirmed(args),
                )
            )
        if name == "list_external_skills":
            return ToolResult(True, self.loader.list_external(str(args.get("source") or "")))
        return None

    def context(self) -> str:
        skills = self.loader.discover(self.active_modules)
        if not skills:
            return ""
        lines = [
            "Skills available — call `load_skill` when the request matches:"
        ]
        for skill in skills:
            lines.append(f"- `{skill.name}` [{skill.scope}]: {skill.description}")
        return "\n".join(lines)


def _confirmed(args: dict) -> bool:
    value = args.get("confirm")
    return value is True or str(value or "").strip().lower() == "true"


def _result(text: str) -> ToolResult:
    ok = not text.startswith(("Error:", "CONFIRMATION REQUIRED"))
    return ToolResult(ok, text)
