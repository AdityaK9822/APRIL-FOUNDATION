"""Persona sources: committed profiles and deployed trees.

The persona is resolved in one place: a deployed tree wins for text, a committed
profile supplies the derived temperature, and an unknown id raises loudly.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from . import kernel
from .guards import PersonaGuard


@dataclass(frozen=True)
class Persona:
    name: str
    system_text: str
    guardrails: str
    temperature: float
    source: str

    def check(self, command: str) -> tuple[bool, str]:
        return PersonaGuard(self.guardrails).check(command)

    def attested_rules(self) -> list[str]:
        return PersonaGuard(self.guardrails).attested_rules()


@runtime_checkable
class ProfileSource(Protocol):
    def names(self) -> list[str]: ...

    def load(self, name: str) -> kernel.Profile: ...


class FileProfiles:
    """Committed ``profiles/*.json``."""

    def __init__(self, directory: str | Path | None = None):
        self.directory = Path(directory or kernel.PROFILES_DIR)

    def names(self) -> list[str]:
        return sorted(kernel.load_profiles(self.directory))

    def load(self, name: str) -> kernel.Profile:
        return kernel.load_profile(name, self.directory)


class DeployedPersonas:
    """Read a deployed tree's c1/c2.

    shortcut: APRIL surface file names only; a zcode-surface deployment folds
    both into AGENTS.md and needs the surface-aware resolver in Phase 8.
    """

    def __init__(self, root: str | Path = ".ade/harnesses"):
        self.root = Path(root)

    def load(self, name: str, domain: str = "default") -> Persona | None:
        tree = self.root / name / domain
        system = tree / "systemprompt.md"
        if not system.is_file():
            return None
        guardrails = tree / "guardrails.md"
        return Persona(
            name=str(name),
            system_text=system.read_text(encoding="utf-8", errors="replace").strip(),
            guardrails=(
                guardrails.read_text(encoding="utf-8", errors="replace").strip()
                if guardrails.is_file()
                else ""
            ),
            temperature=0.7,
            source=f"deployed:{domain}",
        )


def compiled_guardrails(profile) -> str:
    protocols = kernel.persona_of(profile).protocols
    if not protocols:
        return ""
    lines = ["## Protocols (non-negotiable)"]
    lines += [f"{index + 1}. {rule}" for index, rule in enumerate(protocols)]
    lines += ["", kernel.PROTOCOL_OVERRIDE_LINE]
    return "\n".join(lines)


def compile_profile(profile: kernel.Profile) -> Persona:
    return Persona(
        name=profile.id,
        system_text=kernel.compile_persona(profile).strip(),
        guardrails=compiled_guardrails(profile),
        temperature=float(kernel.derive(profile).temperature),
        source=f"profile:{profile.id}",
    )


def resolve(
    name: str,
    profiles: ProfileSource | None = None,
    deployed: DeployedPersonas | None = None,
    domain: str = "default",
) -> Persona:
    """Deployed text wins; the committed profile supplies temperature."""
    if profiles is None:
        raise ValueError("resolve requires a profile source")
    profile = profiles.load(str(name))  # unknown id raises FileNotFoundError
    compiled = compile_profile(profile)
    if deployed is not None:
        deployed_persona = deployed.load(name, domain)
        if deployed_persona is not None:
            return Persona(
                name=deployed_persona.name,
                system_text=deployed_persona.system_text,
                guardrails=deployed_persona.guardrails or compiled.guardrails,
                temperature=compiled.temperature,
                source=deployed_persona.source,
            )
    return compiled
