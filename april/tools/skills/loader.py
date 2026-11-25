"""SKILL.md discovery, CRUD, import, scope, and symlink rejection.

``scope: user`` skills load into the global library; ``scope: module:<name>``
skills load only while that module is active. A skill whose declared scope does
not match its root is refused, so a module cannot smuggle a global skill.
Writes require explicit ``confirm=true``.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from ..frontmatter import parse_frontmatter

SAFE_KEYS = {"name", "description", "when_to_use", "license", "metadata", "scope"}
_NAME_RX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    scope: str
    path: Path
    body: str
    text: str
    safe_to_autoload: bool


class SkillLoader:
    def __init__(
        self,
        user_roots: list[str | Path] | None = None,
        module_roots: dict[str, str | Path] | None = None,
        external_roots: list[tuple[str, str | Path]] | None = None,
        workspace: str | Path | None = None,
        allow_writes: bool | None = None,
    ):
        self.workspace = Path(workspace or ".").resolve()
        default_user = Path.home() / ".agents" / "skills"
        self.user_roots = [Path(root) for root in (user_roots or [default_user])]
        self.module_roots = {
            name: Path(root) for name, root in (module_roots or {}).items()
        }
        self.external_roots = [
            (str(label), Path(root))
            for label, root in (
                external_roots
                if external_roots is not None
                else default_external_roots(self.workspace)
            )
        ]
        if allow_writes is None:
            import os

            allow_writes = os.getenv("APRIL_SKILL_WRITE", "1").strip() != "0"
        self.allow_writes = bool(allow_writes)

    # -- discovery ----------------------------------------------------------

    def discover(self, active_modules: set[str] | frozenset[str] = frozenset()) -> list[Skill]:
        found: dict[str, Skill] = {}
        for root in self.user_roots:
            self._scan(root, "user", found)
        for module in sorted(active_modules):
            root = self.module_roots.get(module)
            if root is not None:
                self._scan(root, f"module:{module}", found)
        return sorted(found.values(), key=lambda skill: skill.name.lower())

    def load(
        self, name: str, active_modules: set[str] | frozenset[str] = frozenset()
    ) -> Skill | None:
        return {skill.name: skill for skill in self.discover(active_modules)}.get(
            str(name or "").strip()
        )

    def find(self, name: str, scope: str = "") -> Skill | None:
        clean = str(name or "").strip()
        if scope:
            root = self.target_root(scope)
            return self._find_in(root, clean, scope)
        for skill in self.discover():  # user scope
            if skill.name == clean:
                return skill
        return None

    def _find_in(self, root: Path, name: str, scope: str) -> Skill | None:
        skill_md = root / name / "SKILL.md"
        if not skill_md.is_file() or skill_md.is_symlink():
            return None
        return self._read(skill_md, scope)

    def _scan(self, root: Path, default_scope: str, found: dict[str, Skill]) -> None:
        if not root.is_dir():
            return
        root_resolved = root.resolve()
        for skill_md in sorted(root.glob("*/SKILL.md")):
            if skill_md.is_symlink() or skill_md.parent.is_symlink():
                continue
            if root_resolved not in skill_md.resolve().parents:
                continue
            skill = self._read(skill_md, default_scope)
            if skill is not None:
                found.setdefault(skill.name, skill)

    def _read(
        self, skill_md: Path, default_scope: str, enforce_scope: bool = True
    ) -> Skill | None:
        try:
            text = skill_md.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        front, body = parse_frontmatter(text)
        name = str(front.get("name") or skill_md.parent.name).strip()
        description = str(front.get("description") or "").strip()
        if not name or not description:
            return None
        declared = str(front.get("scope") or "").strip()
        if enforce_scope and declared:
            if not (declared == "user" or declared.startswith("module:")):
                return None
            if declared != default_scope:
                return None
        return Skill(
            name=name,
            description=description,
            scope=default_scope,
            path=skill_md,
            body=body,
            text=text,
            safe_to_autoload=set(front) <= SAFE_KEYS,
        )

    # -- writes -------------------------------------------------------------

    def target_root(self, scope: str) -> Path:
        clean = str(scope or "user").strip().lower()
        if clean == "workspace":
            clean = "user"
        if clean == "user":
            return self.user_roots[0]
        if clean.startswith("module:"):
            name = clean.split(":", 1)[1]
            if name not in self.module_roots:
                raise ValueError(f"unknown module scope: {clean!r}")
            return self.module_roots[name]
        raise ValueError(f"unknown skill scope: {scope!r}")

    def create(
        self,
        name: str,
        description: str,
        body: str,
        scope: str = "user",
        confirm: bool = False,
    ) -> str:
        name = str(name or "").strip()
        description = str(description or "").strip()
        body = str(body or "").strip()
        problem = self._validate_name(name)
        if problem:
            return f"Error: {problem}."
        if not description:
            return "Error: description is required."
        if not body:
            return "Error: body is required."
        if not confirm:
            return self._confirmation(f"create skill '{name}' in {scope} scope")
        try:
            root = self.target_root(scope)
        except ValueError as exc:
            return f"Error: {exc}."
        target = root / name
        if target.exists():
            return f"Error: skill '{name}' already exists at {target}."
        try:
            target.mkdir(parents=True, exist_ok=False)
            (target / "SKILL.md").write_text(
                _skill_document(name, description, body, scope), encoding="utf-8"
            )
        except OSError as exc:
            return f"Error creating skill: {exc}"
        return f"Created skill '{name}' ({scope}) at {target / 'SKILL.md'}."

    def update(
        self,
        name: str,
        description: str | None = None,
        body: str | None = None,
        scope: str = "",
        confirm: bool = False,
    ) -> str:
        record = self.find(name, scope)
        if record is None:
            return f"Error: unknown skill '{name}'."
        if not confirm:
            return self._confirmation(f"update skill '{name}'")
        new_description = (
            record.description if description is None else str(description).strip()
        )
        new_body = record.body if body is None else str(body).strip()
        try:
            record.path.write_text(
                _skill_document(record.name, new_description, new_body, record.scope),
                encoding="utf-8",
            )
        except OSError as exc:
            return f"Error updating skill: {exc}"
        return f"Updated skill '{record.name}' at {record.path}."

    def delete(self, name: str, scope: str = "", confirm: bool = False) -> str:
        record = self.find(name, scope)
        if record is None:
            return f"Error: unknown skill '{name}'."
        if not confirm:
            return self._confirmation(f"delete skill '{name}'")
        directory = record.path.parent.resolve()
        try:
            root = self.target_root(record.scope).resolve()
            directory.relative_to(root)
        except (ValueError, OSError):
            return "Error: refusing to delete a skill outside its scope root."
        try:
            shutil.rmtree(directory)
        except OSError as exc:
            return f"Error deleting skill: {exc}"
        return f"Deleted skill '{record.name}' ({record.scope})."

    # -- external skills ----------------------------------------------------

    def list_external(self, source: str = "") -> str:
        items: list[dict[str, str]] = []
        for label, root in self.external_roots:
            if source and source not in (label, str(root)):
                continue
            if not root.is_dir():
                continue
            for skill_md in sorted(root.glob("*/SKILL.md")):
                record = self._read(skill_md, "external", enforce_scope=False)
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

    def import_skill(
        self,
        name: str,
        source: str = "",
        scope: str = "user",
        confirm: bool = False,
    ) -> str:
        name = str(name or "").strip()
        candidates = self._external_candidates(name, source)
        if not candidates:
            wanted = f" in source {source!r}" if source else ""
            return f"Error: no external skill named '{name}' found{wanted}."
        if len(candidates) > 1:
            listing = ", ".join(f"{label}:{path}" for label, path, _ in candidates)
            return (
                f"Error: skill '{name}' exists in multiple sources ({listing}); "
                "pass source=<label or path>."
            )
        if not confirm:
            return self._confirmation(f"import skill '{name}' into {scope} scope")
        label, skill_dir, record = candidates[0]
        try:
            root = self.target_root(scope)
        except ValueError as exc:
            return f"Error: {exc}."
        target = root / record.name
        if target.exists():
            return f"Error: skill '{record.name}' already exists at {target}."
        try:
            root.mkdir(parents=True, exist_ok=True)
            shutil.copytree(skill_dir, target)
        except OSError as exc:
            return f"Error importing skill: {exc}"
        return f"Imported skill '{record.name}' from {label} into {target}."

    def _external_candidates(self, name: str, source: str):
        candidates = []
        for label, root in self.external_roots:
            if not root.is_dir():
                continue
            skill_md = root / name / "SKILL.md"
            if not skill_md.is_file():
                continue
            if source and source not in (label, str(root), str(skill_md.parent)):
                continue
            record = self._read(skill_md, "external", enforce_scope=False)
            if record is not None:
                candidates.append((label, skill_md.parent, record))
        return candidates

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _validate_name(name: str) -> str | None:
        if not name or not _NAME_RX.fullmatch(name):
            return "skill names must match [A-Za-z0-9][A-Za-z0-9._-]*"
        if ".." in name:
            return "skill names cannot contain '..'"
        return None

    @staticmethod
    def _confirmation(action: str) -> str:
        return (
            f"CONFIRMATION REQUIRED: {action}. Ask the user to approve explicitly, "
            "then call the tool again with confirm=true."
        )


def _skill_document(name: str, description: str, body: str, scope: str) -> str:
    if "\n" in description:
        rendered = "|\n" + "\n".join(f"  {line}" for line in description.splitlines())
    else:
        rendered = description
    return (
        f"---\nname: {name}\ndescription: {rendered}\nscope: {scope}\n---\n\n"
        f"{body.rstrip()}\n"
    )


def default_external_roots(workspace: Path) -> list[tuple[str, Path]]:
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
        ("continue", home / ".continue" / "skills"),
        ("goose", home / ".config" / "goose" / "skills"),
        ("qwen", home / ".qwen" / "skills"),
        ("qoder", home / ".qoder" / "skills"),
        ("windsurf", home / ".codeium" / "windsurf" / "skills"),
        ("trae", home / ".trae" / "skills"),
        ("kiro", home / ".kiro" / "skills"),
        ("roo", home / ".roo" / "skills"),
        ("codebuddy", home / ".codebuddy" / "skills"),
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
