"""Append-only local SQLite audit trail for tool execution metadata."""
from __future__ import annotations
import json
from pathlib import Path
import sqlite3
import threading
from typing import Any

class SQLiteToolAuditLog:
    """Store execution metadata without recording returned file contents."""
    def __init__(self, database: str | Path) -> None:
        self.database = ":memory:" if str(database) == ":memory:" else str(Path(database).expanduser().resolve())
        if self.database != ":memory:":
            Path(self.database).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.database, timeout=5.0, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA busy_timeout = 5000")
        with self._db:
            self._db.execute("""
                CREATE TABLE IF NOT EXISTS tool_audit (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                    step INTEGER NOT NULL,
                    tool TEXT NOT NULL,
                    arguments_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    summary TEXT NOT NULL
                )
            """)
    def append(self, event: dict[str, Any]) -> None:
        required = {"step", "tool", "arguments", "status", "summary"}
        if not isinstance(event, dict) or set(event) != required:
            raise ValueError("Audit event has an invalid schema")
        if not isinstance(event["step"], int) or event["step"] < 1:
            raise ValueError("Audit step must be a positive integer")
        if not isinstance(event["tool"], str) or len(event["tool"]) > 80:
            raise ValueError("Invalid audit tool name")
        if not isinstance(event["arguments"], dict):
            raise ValueError("Audit arguments must be an object")
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO tool_audit(step,tool,arguments_json,status,summary) VALUES(?,?,?,?,?)",
                (event["step"], event["tool"],
                 json.dumps(event["arguments"], ensure_ascii=False, separators=(",", ":"))[:4000],
                 str(event["status"])[:40], str(event["summary"])[:300]),
            )
    def recent(self, *, limit: int = 50) -> list[dict[str, Any]]:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        with self._lock:
            rows = self._db.execute(
                "SELECT event_id,at,step,tool,arguments_json,status,summary FROM tool_audit ORDER BY event_id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        result=[]
        for row in rows:
            item=dict(row)
            item["arguments"] = json.loads(item.pop("arguments_json"))
            result.append(item)
        return result
    def close(self) -> None:
        with self._lock:
            self._db.close()
    def __enter__(self) -> "SQLiteToolAuditLog":
        return self
    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()
