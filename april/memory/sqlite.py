"""SQLite adapter for the memory seam.

Read-compatible with ``core/database.py``'s schema (same tables, indexes and
content-hash dedup), so an existing ``data/april_memory.db`` keeps working.

shortcut: one connection serialised by a lock replaces the old five-connection
pool; revisit only if a second process writes concurrently.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from .base import Memory, MemoryInput, SessionInfo, Turn, memory_hash


class SQLiteMemory:
    """One store: conversations, structured memories, sessions."""

    def __init__(self, path: str | Path = "./data/april_memory.db"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._init_schema()

    # -- infrastructure -----------------------------------------------------

    def _execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cursor = self._conn.execute(sql, params)
            self._conn.commit()
            return cursor

    def _fetchall(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def _fetchone(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def _init_schema(self) -> None:
        self._execute(
            "CREATE TABLE IF NOT EXISTS conversations ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, "
            "role TEXT NOT NULL, content TEXT, "
            "timestamp DATETIME DEFAULT CURRENT_TIMESTAMP, "
            "processed BOOLEAN DEFAULT 0, "
            "created_at DATETIME DEFAULT CURRENT_TIMESTAMP)"
        )
        self._execute(
            "CREATE INDEX IF NOT EXISTS idx_conversations_session "
            "ON conversations(session_id)"
        )
        self._execute(
            "CREATE INDEX IF NOT EXISTS idx_conversations_unprocessed "
            "ON conversations(processed) WHERE processed = 0"
        )
        self._execute(
            "CREATE TABLE IF NOT EXISTS memory ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, "
            "memory_type TEXT NOT NULL, content TEXT NOT NULL, "
            "confidence REAL DEFAULT 0.5, tags TEXT, "
            "source_message_ids TEXT, content_hash TEXT, "
            "created_at DATETIME DEFAULT CURRENT_TIMESTAMP)"
        )
        self._execute("CREATE INDEX IF NOT EXISTS idx_memory_type ON memory(memory_type)")
        self._execute("CREATE INDEX IF NOT EXISTS idx_memory_session ON memory(session_id)")

        columns = {row[1] for row in self._fetchall("PRAGMA table_info(memory)")}
        if columns and "content_hash" not in columns:
            self._execute("ALTER TABLE memory ADD COLUMN content_hash TEXT")

        rows = self._fetchall(
            "SELECT id, memory_type, content FROM memory WHERE content_hash IS NULL"
        )
        for row in rows:
            try:
                content = json.loads(row["content"])
            except (json.JSONDecodeError, TypeError):
                continue
            self._execute(
                "UPDATE memory SET content_hash = ? WHERE id = ?",
                (memory_hash(row["memory_type"], content), row["id"]),
            )

        seen = set()
        for row in self._fetchall(
            "SELECT id, memory_type, content_hash FROM memory "
            "WHERE content_hash IS NOT NULL ORDER BY id ASC"
        ):
            key = (row["memory_type"], row["content_hash"])
            if key in seen:
                self._execute("DELETE FROM memory WHERE id = ?", (row["id"],))
            else:
                seen.add(key)

        self._execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_content_hash "
            "ON memory(memory_type, content_hash) WHERE content_hash IS NOT NULL"
        )
        self._execute(
            "CREATE TABLE IF NOT EXISTS sessions ("
            "id TEXT PRIMARY KEY, created_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
            "metadata TEXT)"
        )

    # -- conversations ------------------------------------------------------

    def remember(self, turn: Turn) -> int:
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO sessions (id, metadata) VALUES (?, ?)",
                (turn.session_id, "{}"),
            )
            cursor = self._conn.execute(
                "INSERT INTO conversations (session_id, role, content, timestamp) "
                "VALUES (?, ?, ?, ?)",
                (turn.session_id, turn.role, turn.content, datetime.now().isoformat()),
            )
            self._conn.commit()
            return cursor.lastrowid

    def history(self, session_id: str, limit: int = 50) -> list[dict]:
        rows = self._fetchall(
            "SELECT * FROM conversations WHERE session_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        )
        return [dict(row) for row in reversed(rows)]

    def unprocessed(self, session_id: str | None = None) -> list[dict]:
        if session_id:
            rows = self._fetchall(
                "SELECT * FROM conversations WHERE session_id = ? AND processed = 0 "
                "ORDER BY id ASC",
                (session_id,),
            )
        else:
            rows = self._fetchall(
                "SELECT * FROM conversations WHERE processed = 0 ORDER BY id ASC"
            )
        return [dict(row) for row in rows]

    def mark_processed(self, ids: list[int]) -> None:
        if not ids:
            return
        placeholders = ",".join("?" * len(ids))
        self._execute(
            f"UPDATE conversations SET processed = 1 WHERE id IN ({placeholders})", tuple(ids)
        )

    # -- structured memory --------------------------------------------------

    def add_memory(self, memory: MemoryInput) -> int:
        content_hash = memory_hash(memory.memory_type, memory.content)
        existing = self._fetchone(
            "SELECT id FROM memory WHERE memory_type = ? AND content_hash = ?",
            (memory.memory_type, content_hash),
        )
        if existing:
            return existing["id"]
        with self._lock:
            try:
                cursor = self._conn.execute(
                    "INSERT INTO memory (session_id, memory_type, content, confidence, "
                    "tags, source_message_ids, content_hash) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        memory.session_id,
                        memory.memory_type,
                        json.dumps(memory.content),
                        memory.confidence,
                        json.dumps(memory.tags),
                        json.dumps(memory.source_ids),
                        content_hash,
                    ),
                )
                self._conn.commit()
                return cursor.lastrowid
            except sqlite3.IntegrityError:
                self._conn.rollback()
                row = self._conn.execute(
                    "SELECT id FROM memory WHERE memory_type = ? AND content_hash = ?",
                    (memory.memory_type, content_hash),
                ).fetchone()
                if row:
                    return row["id"]
                raise

    def recall(self, query: str, k: int = 10) -> list[Memory]:
        like = f"%{query}%"
        rows = self._fetchall(
            "SELECT * FROM memory WHERE content LIKE ? OR tags LIKE ? "
            "ORDER BY created_at DESC LIMIT ?",
            (like, like, k),
        )
        return [self._to_memory(row) for row in rows]

    def session(self, session_id: str) -> SessionInfo | None:
        row = self._fetchone(
            "SELECT s.id, s.created_at, s.metadata, COUNT(c.id) AS message_count "
            "FROM sessions s LEFT JOIN conversations c ON c.session_id = s.id "
            "WHERE s.id = ? GROUP BY s.id, s.created_at, s.metadata",
            (session_id,),
        )
        if not row:
            return None
        data = dict(row)
        try:
            metadata = json.loads(data.get("metadata") or "{}")
        except (json.JSONDecodeError, TypeError):
            metadata = {}
        return SessionInfo(
            id=data["id"],
            created_at=str(data.get("created_at") or ""),
            metadata=metadata,
            message_count=data.get("message_count") or 0,
        )

    def sessions(self, limit: int = 100) -> list[dict]:
        rows = self._fetchall(
            "SELECT s.id, s.created_at, s.metadata, "
            "MIN(c.timestamp) AS first_message_at, "
            "MAX(c.timestamp) AS last_message_at, "
            "COUNT(c.id) AS message_count "
            "FROM sessions s LEFT JOIN conversations c ON c.session_id = s.id "
            "GROUP BY s.id, s.created_at, s.metadata "
            "ORDER BY COALESCE(last_message_at, s.created_at) DESC LIMIT ?",
            (max(1, int(limit)),),
        )
        out: list[dict] = []
        for row in rows:
            item = dict(row)
            try:
                item["metadata"] = json.loads(item.get("metadata") or "{}")
            except (json.JSONDecodeError, TypeError):
                item["metadata"] = {}
            out.append(item)
        return out

    def _to_memory(self, row: sqlite3.Row) -> Memory:
        data = dict(row)
        try:
            content = json.loads(data["content"])
        except (json.JSONDecodeError, TypeError):
            content = {"text": data["content"]}
        try:
            tags = json.loads(data.get("tags") or "[]")
        except (json.JSONDecodeError, TypeError):
            tags = []
        return Memory(
            id=data["id"],
            memory_type=data["memory_type"],
            content=content,
            confidence=data.get("confidence") or 0.5,
            tags=tags,
            created_at=str(data.get("created_at") or ""),
        )

    def close(self) -> None:
        with self._lock:
            self._conn.close()
