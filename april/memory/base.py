"""The memory seam: one conversational store behind a small interface.

Turns (raw conversation) and structured memories (extracted facts/decisions)
live in the same store; retrieval sits behind ``recall``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Optional, Protocol, runtime_checkable


def memory_hash(memory_type: str, content: dict) -> str:
    """Stable dedup key; identical (type, content) pairs hash the same."""
    payload = json.dumps(
        {"type": memory_type, "content": content}, sort_keys=True, ensure_ascii=False
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True)
class Turn:
    session_id: str
    role: str
    content: str


@dataclass(frozen=True)
class MemoryInput:
    memory_type: str
    content: dict
    confidence: float = 0.5
    tags: list[str] = field(default_factory=list)
    session_id: str = ""
    source_ids: list[int] = field(default_factory=list)


@dataclass(frozen=True)
class Memory:
    id: int
    memory_type: str
    content: dict
    confidence: float = 0.5
    tags: list[str] = field(default_factory=list)
    created_at: str = ""


@dataclass(frozen=True)
class SessionInfo:
    id: str
    created_at: str = ""
    metadata: dict = field(default_factory=dict)
    message_count: int = 0


@runtime_checkable
class MemoryStore(Protocol):
    """Persistence seam. SQLite now, vector later; both satisfy this."""

    def remember(self, turn: Turn) -> int: ...

    def recall(self, query: str, k: int = 10) -> list[Memory]: ...

    def history(self, session_id: str, limit: int = 50) -> list[dict]: ...

    def session(self, session_id: str) -> Optional[SessionInfo]: ...

    def sessions(self, limit: int = 100) -> list[dict]: ...

    def unprocessed(self, session_id: Optional[str] = None) -> list[dict]: ...

    def mark_processed(self, ids: list[int]) -> None: ...

    def add_memory(self, memory: MemoryInput) -> int: ...
