"""Run one subagent in a separate process: JSON in, JSON out.

The child reconstructs the model and a builtin toolset from its own settings;
nothing in-process is pickled across the boundary.
"""

from __future__ import annotations

import json
import os
import sys

from april.config import Settings
from april.llm import ChatReply, FakeModel, OpenAIModel
from april.tools.builtin import BuiltinSource
from april.tools.registry import Toolset
from april.tools.subagents.profile import SubagentProfile
from april.tools.subagents.runner import SubagentRunner


def main() -> int:
    payload = json.load(sys.stdin)
    profile = SubagentProfile.from_dict(payload["profile"])
    settings = Settings.from_env()
    if os.getenv("APRIL_SUBAGENT_FAKE", "0") == "1":
        model = FakeModel([ChatReply(text=str(payload.get("fake_reply") or "done"))])
    else:
        model = OpenAIModel(settings.api_key, settings.api_base_url)
    tools = Toolset([BuiltinSource(workspace=payload.get("workspace") or ".")])
    runner = SubagentRunner(model, tools, settings.model)
    output = runner.run(profile, str(payload.get("task") or ""))
    print(json.dumps({"ok": True, "output": output}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
