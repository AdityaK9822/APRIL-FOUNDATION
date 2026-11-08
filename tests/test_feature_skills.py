"""Tests for the skills feature adapter (catalog, CRUD, import)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.feature_adapter import SkillsFeatureAdapter


def _agent() -> SimpleNamespace:
    return SimpleNamespace(config=SimpleNamespace(), _event_bus=None)


def _write_skill(
    root: Path,
    name: str,
    *,
    description: str = "A test skill.",
    body: str = "Follow these steps.",
) -> Path:
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return directory


@pytest.fixture
def workspace(tmp_path):
    return tmp_path / "workspace"


@pytest.fixture
def adapter(workspace):
    return SkillsFeatureAdapter(
        workspace=workspace,
        user_root=tmp_path_user(workspace),
        external_roots=[],
        allow_writes=True,
    )


def tmp_path_user(workspace: Path) -> Path:
    return workspace.parent / "user-skills"


def test_catalog_and_load(workspace):
    skill_dir = _write_skill(
        workspace / ".agents" / "skills",
        "review",
        description="Review code for defects.",
        body="Read the diff, then list findings.",
    )
    (skill_dir / "scripts").mkdir()
    (skill_dir / "scripts" / "check.sh").write_text("#!/bin/sh\n", encoding="utf-8")

    adapter = SkillsFeatureAdapter(
        workspace=workspace,
        user_root=tmp_path_user(workspace),
        external_roots=[],
        allow_writes=True,
    )
    agent = _agent()

    catalog = adapter.pre_call(agent, "review this")
    assert "review" in catalog
    assert "Review code for defects." in catalog

    loaded = adapter.dispatch("load_skill", {"name": "review"}, agent)
    assert loaded.startswith("# Skill: review")
    assert "Read the diff, then list findings." in loaded
    assert "scripts/check.sh" in loaded

    missing = adapter.dispatch("load_skill", {"name": "ghost"}, agent)
    assert "unknown skill" in missing.lower()


def test_workspace_scope_wins_but_user_scope_is_addressable(workspace):
    user_root = tmp_path_user(workspace)
    _write_skill(user_root, "review", description="user copy")
    _write_skill(workspace / ".agents" / "skills", "review", description="workspace copy")

    adapter = SkillsFeatureAdapter(
        workspace=workspace,
        user_root=user_root,
        external_roots=[],
        allow_writes=True,
    )
    agent = _agent()

    assert "workspace copy" in adapter.pre_call(agent, "review")
    loaded = adapter.dispatch("load_skill", {"name": "review"}, agent)
    assert "workspace copy" in loaded

    # A write addressed to user scope targets the user copy, not the
    # workspace override.
    updated = adapter.dispatch(
        "update_skill",
        {"name": "review", "scope": "user", "body": "user body", "confirm": True},
        agent,
    )
    assert "Updated skill 'review'" in updated
    assert "user body" in (user_root / "review" / "SKILL.md").read_text()
    assert "workspace copy" in (
        workspace / ".agents" / "skills" / "review" / "SKILL.md"
    ).read_text()


def test_create_update_delete_require_confirmation(workspace):
    adapter = SkillsFeatureAdapter(
        workspace=workspace,
        user_root=tmp_path_user(workspace),
        external_roots=[],
        allow_writes=True,
    )
    agent = _agent()
    target = workspace / ".agents" / "skills" / "new-skill" / "SKILL.md"

    blocked = adapter.dispatch(
        "create_skill",
        {"name": "new-skill", "description": "New.", "body": "Body."},
        agent,
    )
    assert "CONFIRMATION REQUIRED" in blocked
    assert not target.exists()

    created = adapter.dispatch(
        "create_skill",
        {
            "name": "new-skill",
            "description": "New.",
            "body": "Body.",
            "confirm": True,
        },
        agent,
    )
    assert "Created skill 'new-skill'" in created
    assert target.is_file()
    assert "description: New." in target.read_text()

    updated = adapter.dispatch(
        "update_skill",
        {"name": "new-skill", "body": "Updated body.", "confirm": True},
        agent,
    )
    assert "Updated skill 'new-skill'" in updated
    assert "Updated body." in target.read_text()

    still_there = adapter.dispatch("delete_skill", {"name": "new-skill"}, agent)
    assert "CONFIRMATION REQUIRED" in still_there
    assert target.exists()

    deleted = adapter.dispatch(
        "delete_skill", {"name": "new-skill", "confirm": True}, agent
    )
    assert "Deleted skill 'new-skill'" in deleted
    assert not target.exists()


def test_user_scope_create_and_invalid_name(workspace):
    user_root = tmp_path_user(workspace)
    adapter = SkillsFeatureAdapter(
        workspace=workspace,
        user_root=user_root,
        external_roots=[],
        allow_writes=True,
    )
    agent = _agent()

    created = adapter.dispatch(
        "create_skill",
        {
            "name": "user-skill",
            "description": "User scope.",
            "body": "Body.",
            "scope": "user",
            "confirm": True,
        },
        agent,
    )
    assert "Created skill 'user-skill' (user)" in created
    assert (user_root / "user-skill" / "SKILL.md").is_file()

    rejected = adapter.dispatch(
        "create_skill",
        {
            "name": "../escape",
            "description": "Bad.",
            "body": "Body.",
            "confirm": True,
        },
        agent,
    )
    assert "Error:" in rejected
    assert not (workspace / ".agents" / "skills" / "../escape").exists()


def test_writes_can_be_disabled(workspace):
    adapter = SkillsFeatureAdapter(
        workspace=workspace,
        user_root=tmp_path_user(workspace),
        external_roots=[],
        allow_writes=False,
    )
    agent = _agent()

    names = [schema["function"]["name"] for schema in adapter.tool_schemas(agent)]
    assert "load_skill" in names
    assert "create_skill" not in names

    blocked = adapter.dispatch(
        "create_skill",
        {"name": "x", "description": "x", "body": "x", "confirm": True},
        agent,
    )
    assert "disabled" in blocked


def test_import_copies_from_local_agent_and_requires_confirmation(workspace, tmp_path):
    external = tmp_path / "external" / "skills"
    _write_skill(external, "imported", description="From another agent.")
    user_root = tmp_path_user(workspace)
    adapter = SkillsFeatureAdapter(
        workspace=workspace,
        user_root=user_root,
        external_roots=[("claude", external)],
        allow_writes=True,
    )
    agent = _agent()

    listing = adapter.dispatch("list_external_skills", {}, agent)
    items = json.loads(listing)
    assert items[0]["source"] == "claude"
    assert items[0]["name"] == "imported"
    assert items[0]["description"] == "From another agent."

    blocked = adapter.dispatch(
        "import_skill", {"name": "imported", "source": "claude"}, agent
    )
    assert "CONFIRMATION REQUIRED" in blocked
    assert not (workspace / ".agents" / "skills" / "imported").exists()

    imported = adapter.dispatch(
        "import_skill",
        {
            "name": "imported",
            "source": "claude",
            "scope": "workspace",
            "confirm": True,
        },
        agent,
    )
    assert "Imported skill 'imported'" in imported
    target = workspace / ".agents" / "skills" / "imported" / "SKILL.md"
    assert target.is_file()
    assert "From another agent." in target.read_text()

    duplicate = adapter.dispatch(
        "import_skill",
        {
            "name": "imported",
            "source": "claude",
            "scope": "workspace",
            "confirm": True,
        },
        agent,
    )
    assert "already exists" in duplicate


def test_ambiguous_import_requires_source(workspace, tmp_path):
    source_a = tmp_path / "a" / "skills"
    source_b = tmp_path / "b" / "skills"
    _write_skill(source_a, "shared", description="A")
    _write_skill(source_b, "shared", description="B")
    adapter = SkillsFeatureAdapter(
        workspace=workspace,
        user_root=tmp_path_user(workspace),
        external_roots=[("a", source_a), ("b", source_b)],
        allow_writes=True,
    )
    agent = _agent()

    result = adapter.dispatch(
        "import_skill", {"name": "shared", "confirm": True}, agent
    )
    assert "multiple sources" in result
    assert "pass source" in result


def test_folded_description_is_parsed(workspace):
    directory = workspace / ".agents" / "skills" / "folded"
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        "---\n"
        "name: folded\n"
        "description: >\n"
        "  Use this skill when the task\n"
        "  needs folded frontmatter.\n"
        "---\n\n"
        "Body.\n",
        encoding="utf-8",
    )
    adapter = SkillsFeatureAdapter(
        workspace=workspace,
        user_root=tmp_path_user(workspace),
        external_roots=[],
        allow_writes=True,
    )

    catalog = adapter.pre_call(_agent(), "anything")
    assert "Use this skill when the task needs folded frontmatter." in catalog
