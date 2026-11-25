"""Subagent profiles: frontmatter plus a body used as the system prompt."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..frontmatter import parse_frontmatter


@dataclass(frozen=True)
class SubagentProfile:
    name: str
    description: str
    system_prompt: str
    tools: tuple[str, ...] = ("*",)
    disallowed_tools: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    model: str = ""
    permission_mode: str = ""
    max_turns: int = 0
    source: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "system_prompt": self.system_prompt,
            "tools": list(self.tools),
            "disallowed_tools": list(self.disallowed_tools),
            "skills": list(self.skills),
            "model": self.model,
            "permission_mode": self.permission_mode,
            "max_turns": self.max_turns,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SubagentProfile":
        return cls(
            name=str(data["name"]),
            description=str(data["description"]),
            system_prompt=str(data.get("system_prompt") or ""),
            tools=tuple(data.get("tools") or ("*",)),
            disallowed_tools=tuple(data.get("disallowed_tools") or ()),
            skills=tuple(data.get("skills") or ()),
            model=str(data.get("model") or ""),
            permission_mode=str(data.get("permission_mode") or ""),
            max_turns=int(data.get("max_turns") or 0),
            source=str(data.get("source") or ""),
        )


def parse_profile(path: str | Path, source: str = "user") -> SubagentProfile | None:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    front, body = parse_frontmatter(text)
    name = str(front.get("name") or "").strip()
    description = str(front.get("description") or "").strip()
    if not name or not description:
        return None
    permission = str(
        front.get("permissionMode") or front.get("permission_mode") or ""
    ).strip()
    # Project profiles cannot grant bypass; keep the ZCode safety rule.
    if source == "project" and permission == "bypassPermissions":
        permission = ""
    try:
        max_turns = int(front.get("maxTurns") or front.get("max_turns") or 0)
    except (TypeError, ValueError):
        max_turns = 0
    return SubagentProfile(
        name=name,
        description=description,
        system_prompt=body.strip(),
        tools=_as_tuple(front.get("tools"), ("*",)),
        disallowed_tools=_as_tuple(
            front.get("disallowedTools") or front.get("disallowed_tools")
        ),
        skills=_as_tuple(front.get("skills")),
        model=str(front.get("model") or ""),
        permission_mode=permission,
        max_turns=max_turns,
        source=source,
    )


def discover_profiles(
    roots: list[tuple[str, str | Path]],
) -> list[SubagentProfile]:
    found: dict[str, SubagentProfile] = {}
    for source, root in roots:
        root = Path(root)
        if not root.is_dir():
            continue
        for path in sorted(root.glob("*.md")):
            profile = parse_profile(path, source)
            if profile is not None:
                found.setdefault(profile.name, profile)
    return sorted(found.values(), key=lambda profile: profile.name)


def _as_tuple(value, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    if isinstance(value, list):
        return tuple(str(item) for item in value)
    if isinstance(value, str) and value.strip():
        return tuple(part.strip() for part in value.split(",") if part.strip())
    return default
