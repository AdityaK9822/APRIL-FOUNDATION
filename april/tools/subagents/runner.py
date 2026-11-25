"""Run one subagent: a bounded model/tool loop over the injected seams."""

from __future__ import annotations

import json
import threading
from typing import Any

from ...llm import ChatRequest, Model
from .profile import SubagentProfile


class SubagentRunner:
    """Synchronous subagent loop. ``tools`` must not contain the subagent source."""

    def __init__(
        self,
        model: Model,
        tools: Any,
        default_model: str,
        max_turns: int = 8,
    ):
        if any(
            getattr(source, "name", "") == "subagents"
            for source in getattr(tools, "sources", [])
        ):
            raise ValueError(
                "a subagent runner cannot use a toolset that includes the subagents source"
            )
        self.model = model
        self.tools = tools
        self.default_model = default_model
        self.max_turns = max_turns

    def run(
        self,
        profile: SubagentProfile,
        task: str,
        cancel: threading.Event | None = None,
    ) -> str:
        schemas = self._schemas(profile)
        messages: list[dict] = [
            {"role": "system", "content": profile.system_prompt},
            {"role": "user", "content": task},
        ]
        last = ""
        for _ in range(profile.max_turns or self.max_turns):
            if cancel is not None and cancel.is_set():
                return last or "(cancelled)"
            reply = self.model.complete(
                ChatRequest(
                    model=profile.model or self.default_model,
                    messages=messages,
                    tools=[schema.to_openai() for schema in schemas],
                )
            )
            last = reply.text
            if not reply.tool_calls:
                return reply.text
            messages.append(
                {
                    "role": "assistant",
                    "content": reply.text,
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": call.name,
                                "arguments": call.arguments,
                            },
                        }
                        for call in reply.tool_calls
                    ],
                }
            )
            for call in reply.tool_calls:
                try:
                    args = json.loads(call.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = self.tools.dispatch(call.name, args)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": result.content,
                    }
                )
        return last

    def _schemas(self, profile: SubagentProfile):
        schemas = self.tools.schemas()
        if profile.tools and "*" not in profile.tools:
            schemas = [schema for schema in schemas if schema.name in profile.tools]
        if profile.disallowed_tools:
            schemas = [
                schema
                for schema in schemas
                if schema.name not in profile.disallowed_tools
            ]
        return schemas
