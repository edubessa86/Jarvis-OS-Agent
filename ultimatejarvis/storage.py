"""Small SQLite-backed task history using only the Python standard library."""
from __future__ import annotations

from pathlib import Path
import sqlite3
import threading
from typing import Any

from .orchestrator import TaskState, _TRANSITIONS, _now


class TaskStorageError(RuntimeError):
    """Raised when persistent task storage cannot complete an operation."""


class SQLiteTaskStore:
    """Persist task state and append-only state events in a local SQLite database.

    The database stores user task prompts, so callers should choose a private
    local path and avoid passing secrets or credentials as prompts.
    """

    def __init__(self, database: str | Path) -> None:
        self.database = ":memory:" if str(database) == ":memory:" else str(Path(database).expanduser().resolve())
        if self.database != ":memory:":
            Path(self.database).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.database, timeout=5.0, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA busy_timeout = 5000")
        self._initialize()

    def _initialize(self) -> None:
        with self._lock, self._connection:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    prompt TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    error TEXT
                );
                CREATE TABLE IF NOT EXISTS task_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
                    at TEXT NOT NULL,
                    state TEXT NOT NULL,
                    message TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_task_events_task_id_event_id
                    ON task_events(task_id, event_id);
                """
            )

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> "SQLiteTaskStore":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    def create(self, task_id: str, prompt: str, *, max_prompt_chars: int = 20_000) -> dict[str, Any]:
        if not task_id or len(task_id) > 80 or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for ch in task_id):
            raise ValueError("Invalid task_id")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt must be a non-empty string")
        if len(prompt) > max_prompt_chars:
            raise ValueError("prompt exceeds configured size limit")
        now = _now()
        try:
            with self._lock, self._connection:
                self._connection.execute(
                    "INSERT INTO tasks(task_id, prompt, state, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                    (task_id, prompt, TaskState.CREATED.value, now, now),
                )
                self._connection.execute(
                    "INSERT INTO task_events(task_id, at, state, message) VALUES (?, ?, ?, ?)",
                    (task_id, now, TaskState.CREATED.value, "Task created"),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("task_id already exists") from exc
        return self.get(task_id)

    def get(self, task_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._connection.execute(
                "SELECT task_id, prompt, state, created_at, updated_at, error FROM tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown task: {task_id}")
        return dict(row)

    def transition(
        self,
        task_id: str,
        new_state: TaskState,
        message: str,
        *,
        error: str | None = None,
    ) -> dict[str, Any]:
        now = _now()
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                row = self._connection.execute(
                    "SELECT state FROM tasks WHERE task_id = ?", (task_id,)
                ).fetchone()
                if row is None:
                    raise KeyError(f"Unknown task: {task_id}")
                current = TaskState(row["state"])
                if new_state not in _TRANSITIONS[current]:
                    raise ValueError(f"Invalid task transition: {current.value} -> {new_state.value}")
                stored_error = error if new_state is TaskState.FAILED else None
                self._connection.execute(
                    "UPDATE tasks SET state = ?, updated_at = ?, error = ? WHERE task_id = ?",
                    (new_state.value, now, stored_error, task_id),
                )
                self._connection.execute(
                    "INSERT INTO task_events(task_id, at, state, message) VALUES (?, ?, ?, ?)",
                    (task_id, now, new_state.value, str(message)[:500]),
                )
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise
        return self.get(task_id)

    def events(self, task_id: str) -> list[dict[str, Any]]:
        # Validate existence so an unknown task is not confused with an empty history.
        self.get(task_id)
        with self._lock:
            rows = self._connection.execute(
                "SELECT event_id, at, state, message FROM task_events WHERE task_id = ? ORDER BY event_id",
                (task_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_tasks(self, *, limit: int = 50) -> list[dict[str, Any]]:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        with self._lock:
            rows = self._connection.execute(
                "SELECT task_id, prompt, state, created_at, updated_at, error FROM tasks ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]
