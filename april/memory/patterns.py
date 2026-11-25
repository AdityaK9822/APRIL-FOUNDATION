"""Cross-session experience patterns: observed tool chains, reviewable only.

Nothing is auto-injected; a pattern is a candidate until its status changes.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from ..events import EventBus

_SCHEMA = """
CREATE TABLE IF NOT EXISTS experience_patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    pattern TEXT,
    tools TEXT,
    occurrences INTEGER,
    status TEXT DEFAULT 'candidate',
    source TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
)
"""


class PatternStore:
    """A tiny SQLite store for cross-session learning."""

    def __init__(self, path: str | Path = "./data/april_experience.db"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    def record_pattern(self, session_id: str, tools: list[str], pattern: str) -> int:
        tools_json = json.dumps(tools, ensure_ascii=False)
        with self._lock, self._conn:
            existing = self._conn.execute(
                "SELECT id FROM experience_patterns WHERE session_id = ? AND tools = ?",
                (session_id, tools_json),
            ).fetchone()
            if existing:
                self._conn.execute(
                    "UPDATE experience_patterns SET occurrences = occurrences + 1 WHERE id = ?",
                    (existing["id"],),
                )
                return int(existing["id"])
            cursor = self._conn.execute(
                "INSERT INTO experience_patterns (session_id, tools, pattern, occurrences) "
                "VALUES (?, ?, ?, 1)",
                (session_id, tools_json, pattern),
            )
            return int(cursor.lastrowid)

    def set_status(self, pattern_id: int, status: str) -> None:
        if status not in ("candidate", "approved", "rejected"):
            raise ValueError(f"unsupported experience status: {status}")
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE experience_patterns SET status = ? WHERE id = ?",
                (status, int(pattern_id)),
            )

    def patterns(
        self,
        session_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        sql = "SELECT * FROM experience_patterns WHERE 1=1"
        params: list = []
        if session_id:
            sql += " AND session_id = ?"
            params.append(session_id)
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(max(1, int(limit)))
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            try:
                item["tools"] = json.loads(item.get("tools") or "[]")
            except (json.JSONDecodeError, TypeError):
                item["tools"] = []
            out.append(item)
        return out

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def analyze_from_event_memory(
    store: PatternStore, bus: EventBus, session_id: str | None = None
) -> int:
    """Scan the bus history and record the observed tool chain."""
    ordered: list[tuple[str, str]] = []
    for event in bus.get_history(limit=1000):
        payload = event.payload
        tool_name = getattr(payload, "tool_name", None) if payload is not None else None
        if tool_name:
            ordered.append((tool_name, session_id or ""))
    if not ordered:
        return 0
    target_session = session_id or ordered[-1][1]
    tools = sorted({name for name, _sid in ordered})
    return store.record_pattern(target_session, tools, " + ".join(tools))
