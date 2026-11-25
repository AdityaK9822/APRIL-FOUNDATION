"""The composer: the turn sequence and nothing else.

All mechanism lives behind the injected seams; this module only orders the
calls, applies the persona gate, and decides when to learn.
"""

from __future__ import annotations

import json
from typing import AsyncIterator

from ..body.base import DeviceSource, Telemetry
from ..events import EventBus
from ..learning.engine import Engine
from ..learning.target import Trace
from ..llm import ChatReply, ChatRequest, Model, ToolCall
from ..memory.base import MemoryInput, MemoryStore, Turn
from ..memory.extract import MemoryExtractor
from ..persona.profiles import Persona
from ..tools.base import HookEvent, ToolResult
from ..tools.registry import Toolset


class Runtime:
    def __init__(
        self,
        model: Model,
        model_name: str,
        memory: MemoryStore,
        persona: Persona,
        tools: Toolset,
        events: EventBus,
        devices: DeviceSource,
        engines: dict[str, Engine] | None = None,
        *,
        recall_k: int = 8,
        history_k: int = 20,
        learn_targets: tuple[str, ...] = (),
        max_turns: int = 8,
        extractor: MemoryExtractor | None = None,
        base_identity: str = "",
        pricing: dict | None = None,
    ):
        self.model = model
        self.model_name = model_name
        self.memory = memory
        self.persona = persona
        self.tools = tools
        self.events = events
        self.devices = devices
        self.engines = dict(engines or {})
        self.recall_k = max(1, int(recall_k))
        self.history_k = max(1, int(history_k))
        self.learn_targets = tuple(learn_targets)
        self.learn_targets = tuple(learn_targets)
        self.max_turns = max(1, int(max_turns))
        self.extractor = extractor
        self.base_identity = str(base_identity or "").strip()
        self.pricing = dict(pricing or {"input": 0.0, "output": 0.0})
        self.usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "calls": 0,
            "estimated_cost_usd": 0.0,
        }

    def respond(self, user_input: str, session_id: str = "default") -> str:
        submitted = self.tools.fire_hook(
            HookEvent.USER_PROMPT_SUBMIT, {"prompt": user_input}
        )
        if not submitted.allow:
            return f"Blocked by hook: {submitted.reason}"

        context = self.memory.recall(user_input, k=self.recall_k)
        history = self.memory.history(session_id, limit=self.history_k)
        messages = self._messages(context, history, user_input)
        tool_schemas = self.tools.to_openai()
        reply = ChatReply(text="")

        for _ in range(self.max_turns):
            reply = self.model.complete(
                ChatRequest(
                    model=self.model_name,
                    messages=messages,
                    tools=tool_schemas,
                    temperature=self.persona.temperature,
                )
            )
            self._record_usage(reply.usage)
            self.events.emit_message_delta(reply.text, is_assistant=True)
            if not reply.tool_calls:
                break
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
                result = self._dispatch(call)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": result.content,
                    }
                )

        self.memory.remember(Turn(session_id, "user", user_input))
        self.memory.remember(Turn(session_id, "assistant", reply.text))
        self._extract(session_id)
        self.events.emit_message_done("reply", reply.text, is_assistant=True)
        self.tools.fire_hook(HookEvent.STOP, {"reply": reply.text})
        self._learn(session_id, user_input, reply.text)
        return reply.text

    async def stream(
        self, user_input: str, session_id: str = "default"
    ) -> AsyncIterator[str]:
        """Stream the final answer.

        shortcut: a streamed tool call is not handled here; call ``respond`` for
        turns that need tools.
        """
        context = self.memory.recall(user_input, k=self.recall_k)
        history = self.memory.history(session_id, limit=self.history_k)
        messages = self._messages(context, history, user_input)
        text = ""
        usage = None
        async for delta in self.model.stream(
            ChatRequest(
                model=self.model_name,
                messages=messages,
                temperature=self.persona.temperature,
            )
        ):
            if delta.usage is not None:
                usage = delta.usage
            if delta.text:
                text += delta.text
                yield delta.text
        self._record_usage(usage)
        self.memory.remember(Turn(session_id, "user", user_input))
        self.memory.remember(Turn(session_id, "assistant", text))
        self.events.emit_message_done("reply", text, is_assistant=True)

    def device_status(self) -> list[Telemetry]:
        return [self.devices.read(device.id) for device in self.devices.devices()]

    def _dispatch(self, call: ToolCall) -> ToolResult:
        try:
            args = json.loads(call.arguments or "{}")
        except json.JSONDecodeError:
            args = {}
        allowed, reason = self.persona.check(call.arguments or "")
        if not allowed:
            return ToolResult(False, f"Blocked by persona: {reason}")
        self.events.emit_tool_call_start(call.name, args, call.id)
        result = self.tools.dispatch(call.name, args)
        self.events.emit_tool_call_done(
            call.name, result.content, call.id, result.ok
        )
        return result

    def _messages(self, context, history, user_input: str) -> list[dict]:
        system = self.persona.system_text
        if self.base_identity:
            system = (
                f"{self.base_identity}\n\n{system}" if system else self.base_identity
            )
        lines = [
            f"- {memory.content.get('text', '')}"
            for memory in context
            if memory.content.get("text")
        ]
        if lines:
            system = f"{system}\n\nRelevant memories:\n" + "\n".join(lines)
        messages: list[dict] = [{"role": "system", "content": system}]
        for row in history:
            messages.append(
                {
                    "role": str(row.get("role", "user")),
                    "content": str(row.get("content", "")),
                }
            )
        messages.append({"role": "user", "content": user_input})
        return messages

    def _record_usage(self, usage) -> None:
        if usage is None:
            return

        def value(name):
            if isinstance(usage, dict):
                return usage.get(name)
            return getattr(usage, name, None)

        prompt = int(value("prompt_tokens") or 0)
        completion = int(value("completion_tokens") or 0)
        total = int(value("total_tokens") or (prompt + completion))
        self.usage["prompt_tokens"] += prompt
        self.usage["completion_tokens"] += completion
        self.usage["total_tokens"] += total
        self.usage["calls"] += 1
        cost = (prompt / 1000.0) * float(self.pricing.get("input", 0.0)) + (
            completion / 1000.0
        ) * float(self.pricing.get("output", 0.0))
        self.usage["estimated_cost_usd"] = round(
            self.usage["estimated_cost_usd"] + cost, 6
        )

    def _extract(self, session_id: str) -> None:
        if self.extractor is None:
            return
        pending = self.memory.unprocessed(session_id)
        if not pending:
            return
        history = self.memory.history(session_id, limit=1000)
        old = history[: max(0, len(history) - len(pending))]
        extractions = self.extractor.extract_incremental(old, pending)
        source_ids = [row.get("id", 0) for row in pending if row.get("id")]
        for extraction in extractions:
            self.memory.add_memory(
                MemoryInput(
                    memory_type=str(extraction.get("type") or "fact"),
                    content=dict(extraction.get("content") or {}),
                    confidence=float(extraction.get("confidence") or 0.5),
                    tags=list(extraction.get("tags") or []),
                    session_id=session_id,
                    source_ids=source_ids,
                )
            )
        self.memory.mark_processed(source_ids)

    def _learn(self, session_id: str, user_input: str, reply_text: str) -> None:
        for name in self.learn_targets:
            engine = self.engines.get(name)
            if engine is not None:
                engine.run(
                    Trace(
                        session_id=session_id,
                        evidence=f"{user_input}\n{reply_text}",
                    )
                )
