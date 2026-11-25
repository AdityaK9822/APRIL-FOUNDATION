"""APRIL's editable workflow surface (c1/c2/c4 always-on)."""

from __future__ import annotations

from dataclasses import dataclass

from .tree import SurfaceSpec

APRIL_SURFACE = SurfaceSpec(
    name="april",
    always_on=("systemprompt.md", "guardrails.md", "tool_guidance.md"),
    always_on_max=40_000,
    always_on_cap_reason=(
        "always-on files are sent with every request and every round of every "
        "recursion; long material becomes an on-demand .agents/skills/<name>/SKILL.md"
    ),
    skill_dir=".agents/skills",
    skill_name_rule="dir",
    trigger_hint=(
        "add a line in systemprompt.md that names the skill and says when to use it"
    ),
    autotrigger_file="systemprompt.md",
    trigger_line="When relevant, use the `{name}` skill: {desc}.",
    subagent_requires_mode=False,
    plugin_dir=None,
    hooks_file=None,
    hook_events=frozenset(),
    compile_skill_scripts=False,
    reject_empty_always_on=True,
    stubs=(),
)


@dataclass
class AprilSubstrate:
    name: str = "april"
    surface_spec: SurfaceSpec = APRIL_SURFACE

    def surface(self) -> SurfaceSpec:
        return self.surface_spec


APRIL_SUBSTRATE = AprilSubstrate()

__all__ = ["APRIL_SURFACE", "AprilSubstrate", "APRIL_SUBSTRATE"]
