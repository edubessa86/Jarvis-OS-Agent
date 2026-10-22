"""Local-only SQLite memory with optional FTS5 and a LIKE fallback."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import sqlite3
import threading
from typing import Any
import uuid


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class LocalMemory:
    """Store explicitly submitted notes locally; no embeddings or network calls."""

    def __init__(self, database: str | Path, *, max_content_chars: int = 20_000) -> None:
        if max_content_chars < 1:
            raise ValueError("max_content_chars must be positive")
        self.max_content_chars = max_content_chars
        raw_path = str(database)
        self.database = ":memory:" if raw_path == ":memory:" else str(Path(database).expanduser().resolve())
        if self.database != ":memory:":
            Path(self.database).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.database, timeout=5.0, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA busy_timeout = 5000")
        self._fts_available = False
        self._initialize()

    def _initialize(self) -> None:
        with self._lock:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    memory_id TEXT PRIMARY KEY,
                    category TEXT NOT NULL,
                    content TEXT NOT NULL,
                    tags TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_memories_updated_at ON memories(updated_at);
                """
            )
            try:
                self._connection.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(memory_id UNINDEXED, category, content, tags)"
                )
                self._fts_available = True
            except sqlite3.OperationalError:
                self._fts_available = False

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> "LocalMemory":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    def remember(self, content: str, *, category: str = "general", tags: tuple[str, ...] | list[str] = ()) -> dict[str, Any]:
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Memory content must be a non-empty string")
        if len(content) > self.max_content_chars:
            raise ValueError("Memory exceeds configured size limit")
        if not isinstance(category, str) or not category.strip() or len(category) > 80:
            raise ValueError("Category must contain 1-80 characters")
        if not isinstance(tags, (tuple, list)) or not all(
            isinstance(tag, str) and tag.strip() for tag in tags
        ):
            raise ValueError("Tags must be a list or tuple of non-empty strings")
        normalized_tags = sorted({tag.strip()[:80] for tag in tags})
        tags_text = json.dumps(normalized_tags, ensure_ascii=False)
        memory_id = uuid.uuid4().hex
        now = _now()
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                self._connection.execute(
                    "INSERT INTO memories(memory_id, category, content, tags, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (memory_id, category.strip(), content, tags_text, now, now),
                )
                if self._fts_available:
                    self._connection.execute(
                        "INSERT INTO memories_fts(memory_id, category, content, tags) VALUES (?, ?, ?, ?)",
                        (memory_id, category.strip(), content, tags_text),
                    )
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise
        return self.get(memory_id)

    def get(self, memory_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._connection.execute(
                "SELECT memory_id, category, content, tags, created_at, updated_at FROM memories WHERE memory_id = ?",
                (memory_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown memory: {memory_id}")
        result = dict(row)
        result["tags"] = json.loads(result["tags"]) if result["tags"] else []
        return result

    def search(self, query: str, *, limit: int = 10) -> list[dict[str, Any]]:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("Search query must be non-empty")
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        term = query.strip()[:500]
        with self._lock:
            rows = None
            if self._fts_available:
                # Quoting turns user input into a phrase instead of an FTS expression.
                phrase = '"' + term.replace('"', '""') + '"'
                try:
                    rows = self._connection.execute(
                        """
                        SELECT m.memory_id, m.category, m.content, m.tags, m.created_at, m.updated_at
                        FROM memories_fts f JOIN memories m ON m.memory_id = f.memory_id
                        WHERE memories_fts MATCH ?
                        ORDER BY m.updated_at DESC LIMIT ?
                        """,
                        (phrase, limit),
                    ).fetchall()
                except sqlite3.OperationalError:
                    rows = None
            if rows is None:
                like = "%" + term.replace("%", "\\%").replace("_", "\\_") + "%"
                rows = self._connection.execute(
                    """
                    SELECT memory_id, category, content, tags, created_at, updated_at
                    FROM memories
                    WHERE content LIKE ? ESCAPE '\\' OR category LIKE ? ESCAPE '\\' OR tags LIKE ? ESCAPE '\\'
                    ORDER BY updated_at DESC LIMIT ?
                    """,
                    (like, like, like, limit),
                ).fetchall()
        results = []
        for row in rows:
            item = dict(row)
            item["tags"] = json.loads(item["tags"]) if item["tags"] else []
            results.append(item)
        return results

    def delete(self, memory_id: str) -> bool:
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                exists = self._connection.execute(
                    "SELECT 1 FROM memories WHERE memory_id = ?", (memory_id,)
                ).fetchone()
                if exists is None:
                    self._connection.rollback()
                    return False
                if self._fts_available:
                    self._connection.execute("DELETE FROM memories_fts WHERE memory_id = ?", (memory_id,))
                self._connection.execute("DELETE FROM memories WHERE memory_id = ?", (memory_id,))
                self._connection.commit()
                return True
            except Exception:
                self._connection.rollback()
                raise

    def recent(self, *, limit: int = 20) -> list[dict[str, Any]]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        with self._lock:
            rows = self._connection.execute(
                "SELECT memory_id, category, content, tags, created_at, updated_at FROM memories ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["tags"] = item["tags"].split() if item["tags"] else []
            result.append(item)
        return result
