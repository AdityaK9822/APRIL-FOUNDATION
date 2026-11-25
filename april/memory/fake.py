"""In-memory adapter for the memory seam; the offline floor."""

from __future__ import annotations

import json
from datetime import datetime

from .base import Memory, MemoryInput, SessionInfo, Turn, memory_hash


class InMemoryMemoryStore:
    """Dict-backed store. Same contract as SQLiteMemory, no disk."""

    def __init__(self):
        self._turns: list[dict] = []
        self._memories: dict[tuple[str, str], Memory] = {}
        self._next_turn = 1
        self._next_memory = 1

    def remember(self, turn: Turn) -> int:
        turn_id = self._next_turn
        self._next_turn += 1
        self._turns.append(
            {
                "id": turn_id,
                "session_id": turn.session_id,
                "role": turn.role,
                "content": turn.content,
                "timestamp": datetime.now().isoformat(),
                "processed": 0,
            }
        )
        return turn_id

    def recall(self, query: str, k: int = 10) -> list[Memory]:
        needle = query.lower()
        hits = [
            memory
            for memory in self._memories.values()
            if needle in json.dumps(memory.content, ensure_ascii=False).lower()
            or any(needle in tag.lower() for tag in memory.tags)
        ]
        return list(reversed(hits))[:k]

    def history(self, session_id: str, limit: int = 50) -> list[dict]:
        rows = [turn for turn in self._turns if turn["session_id"] == session_id]
        return [dict(turn) for turn in rows[-limit:]]

    def session(self, session_id: str) -> SessionInfo | None:
        rows = [turn for turn in self._turns if turn["session_id"] == session_id]
        if not rows:
            return None
        return SessionInfo(id=session_id, message_count=len(rows))

    def sessions(self, limit: int = 100) -> list[dict]:
        grouped: dict[str, dict] = {}
        for turn in self._turns:
            entry = grouped.setdefault(
                turn["session_id"],
                {
                    "id": turn["session_id"],
                    "created_at": turn["timestamp"],
                    "metadata": {},
                    "message_count": 0,
                },
            )
            entry["message_count"] += 1
            entry["last_message_at"] = turn["timestamp"]
        rows = sorted(
            grouped.values(),
            key=lambda row: row.get("last_message_at") or row["created_at"],
            reverse=True,
        )
        return rows[: max(1, int(limit))]

    def unprocessed(self, session_id: str | None = None) -> list[dict]:
        return [
            dict(turn)
            for turn in self._turns
            if not turn["processed"]
            and (session_id is None or turn["session_id"] == session_id)
        ]

    def mark_processed(self, ids: list[int]) -> None:
        wanted = set(ids)
        for turn in self._turns:
            if turn["id"] in wanted:
                turn["processed"] = 1

    def add_memory(self, memory: MemoryInput) -> int:
        key = (memory.memory_type, memory_hash(memory.memory_type, memory.content))
        existing = self._memories.get(key)
        if existing:
            return existing.id
        memory_id = self._next_memory
        self._next_memory += 1
        self._memories[key] = Memory(
            id=memory_id,
            memory_type=memory.memory_type,
            content=memory.content,
            confidence=memory.confidence,
            tags=memory.tags,
            created_at=datetime.now().isoformat(),
        )
        return memory_id
