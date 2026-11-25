"""One settings object for the new stack. Grows one field per phase."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    """Environment-backed settings. No sub-configs; add fields as modules land."""

    model: str
    api_key: str
    api_base_url: str
    persona: str = ""
    memory_db_path: str = "./data/april_memory.db"
    workspace: str = "."
    learn_targets: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_env(cls, env_file: str | None = None) -> "Settings":
        load_dotenv(env_file)
        targets = tuple(
            part.strip()
            for part in os.getenv("APRIL_LEARN_TARGETS", "").split(",")
            if part.strip()
        )
        return cls(
            model=os.getenv("OPENAI_MODEL", "gemma4:31b-cloud"),
            api_key=os.getenv("OPENAI_API_KEY", ""),
            api_base_url=os.getenv("OPENAI_API_BASE_URL", ""),
            persona=os.getenv("APRIL_PERSONA", "").strip(),
            memory_db_path=os.getenv("APRIL_MEMORY_DB", "./data/april_memory.db"),
            workspace=os.getenv("APRIL_WORKSPACE", "."),
            learn_targets=targets,
        )

    def pricing_per_1k(self) -> dict[str, float]:
        """USD per 1k tokens. Env overrides win; unknown models cost zero."""
        p_in = float(os.getenv("APRIL_COST_IN_PER_1K", "0") or 0)
        p_out = float(os.getenv("APRIL_COST_OUT_PER_1K", "0") or 0)
        if p_in or p_out:
            return {"input": p_in, "output": p_out}
        known = {
            "gpt-4o-mini": {"input": 0.00015, "output": 0.00060},
            "gpt-4o": {"input": 0.00250, "output": 0.01000},
            "glm-5.3-flash-free": {"input": 0.0, "output": 0.0},
            "gemma4:31b-cloud": {"input": 0.0, "output": 0.0},
        }
        return known.get(self.model, {"input": 0.0, "output": 0.0})
