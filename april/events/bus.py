"""The one event bus: typed events, a bounded replay ring, thread-safe.

``emit()`` takes an ``(event_type, payload)`` pair and refuses a non-string
type, so a payload dataclass can never be mistaken for an event — the bug that
silently dropped error/status events in ``core/events.py``.
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Generic, Optional, TypeVar

T = TypeVar("T")

EventCallback = Callable[["Event"], None]


@dataclass(frozen=True)
class Event(Generic[T]):
    """What is stored in history and handed to subscribers."""

    event_type: str
    payload: T = None  # type: ignore[assignment]
    timestamp: datetime = field(default_factory=datetime.now)
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])


# --- payload dataclasses (arrive in Event.payload) -------------------------


@dataclass(frozen=True)
class MessageDelta:
    content: str
    is_assistant: bool
    is_complete: bool = False


@dataclass(frozen=True)
class MessageStart:
    is_assistant: bool
    message_id: str


@dataclass(frozen=True)
class MessageDone:
    message_id: str
    full_content: str
    is_assistant: bool


@dataclass(frozen=True)
class ToolCallStart:
    tool_name: str
    tool_args: dict
    tool_call_id: str


@dataclass(frozen=True)
class ToolCallOutput:
    tool_call_id: str
    text: str
    is_error: bool = False


@dataclass(frozen=True)
class ToolCallDone:
    tool_name: str
    result: str
    tool_call_id: str
    success: bool


@dataclass(frozen=True)
class LogEvent:
    level: str
    source: str
    message: str
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass(frozen=True)
class ErrorEvent:
    error_type: str
    message: str
    details: Optional[dict] = None


@dataclass(frozen=True)
class StatusEvent:
    status: str
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class TelemetryEvent:
    category: str
    source: str
    message: str = ""
    session_id: str = ""
    payload: Optional[dict] = None


@dataclass(frozen=True)
class SessionEvent:
    event_type: str
    session_id: str


class EventBus:
    """Thread-safe bus with a bounded replay ring."""

    def __init__(self, max_history: int = 1000):
        self._callbacks: dict[str, list[tuple[str, EventCallback]]] = {}
        self._async_callbacks: dict[str, list[tuple[str, EventCallback]]] = {}
        self._lock = threading.Lock()
        self._history: list[Event] = []
        self._max_history = max_history

    def subscribe(self, event_type: str, callback: EventCallback) -> str:
        sub_id = str(uuid.uuid4())[:8]
        with self._lock:
            self._callbacks.setdefault(event_type, []).append((sub_id, callback))
        return sub_id

    def subscribe_async(self, event_type: str, callback: EventCallback) -> str:
        sub_id = str(uuid.uuid4())[:8]
        with self._lock:
            self._async_callbacks.setdefault(event_type, []).append((sub_id, callback))
        return sub_id

    def unsubscribe(self, sub_id: str) -> bool:
        with self._lock:
            for table in (self._callbacks, self._async_callbacks):
                for event_type, entries in table.items():
                    kept = [(sid, callback) for sid, callback in entries if sid != sub_id]
                    if len(kept) != len(entries):
                        table[event_type] = kept
                        return True
        return False

    def emit(self, event_type: str, payload: Any = None) -> Event:
        if not isinstance(event_type, str):
            raise TypeError(f"event_type must be str, got {type(event_type).__name__}")
        event = Event(event_type=event_type, payload=payload)
        with self._lock:
            self._history.append(event)
            if len(self._history) > self._max_history:
                self._history = self._history[-self._max_history :]
            sync_callbacks = [cb for _sid, cb in self._callbacks.get(event_type, [])]
            sync_callbacks += [cb for _sid, cb in self._callbacks.get("*", [])]
            async_callbacks = [cb for _sid, cb in self._async_callbacks.get(event_type, [])]
            async_callbacks += [cb for _sid, cb in self._async_callbacks.get("*", [])]
        for callback in sync_callbacks:
            try:
                callback(event)
            except Exception:
                pass
        for callback in async_callbacks:
            try:
                asyncio.create_task(callback(event))
            except Exception:
                pass
        return event

    # -- convenience emitters: one line each, always a real type string ----

    def emit_message_delta(self, content, is_assistant, is_complete=False):
        return self.emit(
            "message.delta",
            MessageDelta(content=content, is_assistant=is_assistant, is_complete=is_complete),
        )

    def emit_message_start(self, is_assistant):
        msg_id = str(uuid.uuid4())[:8]
        self.emit("message.start", MessageStart(is_assistant=is_assistant, message_id=msg_id))
        return msg_id

    def emit_message_done(self, message_id, full_content, is_assistant):
        return self.emit(
            "message.done",
            MessageDone(
                message_id=message_id, full_content=full_content, is_assistant=is_assistant
            ),
        )

    def emit_tool_call_start(self, tool_name, tool_args, tool_call_id):
        return self.emit(
            "tool_call.start",
            ToolCallStart(tool_name=tool_name, tool_args=tool_args, tool_call_id=tool_call_id),
        )

    def emit_tool_call_output(self, tool_call_id, text, is_error=False):
        return self.emit(
            "tool_call.output",
            ToolCallOutput(tool_call_id=tool_call_id, text=text, is_error=is_error),
        )

    def emit_tool_call_done(self, tool_name, result, tool_call_id, success):
        return self.emit(
            "tool_call.done",
            ToolCallDone(
                tool_name=tool_name, result=result, tool_call_id=tool_call_id, success=success
            ),
        )

    def emit_log(self, level, source, message):
        return self.emit("log", LogEvent(level=level, source=source, message=message))

    def emit_telemetry(self, category, source, message="", session_id="", payload=None):
        return self.emit(
            "telemetry",
            TelemetryEvent(
                category=category,
                source=source,
                message=message,
                session_id=session_id,
                payload=payload,
            ),
        )

    def emit_error(self, error_type, message, details=None):
        return self.emit(
            "error", ErrorEvent(error_type=error_type, message=message, details=details)
        )

    def emit_status(self, status, metadata=None):
        return self.emit("status", StatusEvent(status=status, metadata=metadata or {}))

    def get_history(self, event_type: Optional[str] = None, limit: int = 100) -> list[Event]:
        with self._lock:
            history = [
                e for e in self._history if event_type is None or e.event_type == event_type
            ]
            return history[-limit:]

    def clear_history(self) -> None:
        with self._lock:
            self._history.clear()
