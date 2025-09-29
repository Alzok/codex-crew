from __future__ import annotations

import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from mcp.utils import retry_call


@dataclass
class TaskRow:
    task_id: str
    objective: str
    command: str
    status: str
    mode: str
    created_at: float
    updated_at: float
    worker_pid: Optional[int]
    exit_code: Optional[int]
    error: Optional[str]


class TaskStore:
    def __init__(self, db_path: Path | str = "store/tasks.db") -> None:
        resolved_path = Path(os.environ.get("MCP_STORE_PATH", db_path))
        self._db_path = resolved_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._connection = sqlite3.connect(self._db_path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self._connection:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    objective TEXT NOT NULL,
                    command TEXT NOT NULL,
                    status TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    worker_pid INTEGER,
                    exit_code INTEGER,
                    error TEXT
                )
                """
            )

    def upsert_task(
        self,
        *,
        task_id: str,
        objective: str,
        command: str,
        status: str,
        mode: str,
        worker_pid: Optional[int] = None,
        exit_code: Optional[int] = None,
        error: Optional[str] = None,
    ) -> None:
        now = time.time()
        def _op() -> None:
            with self._lock, self._connection:
                self._connection.execute(
                    """
                    INSERT INTO tasks (task_id, objective, command, status, mode, created_at, updated_at, worker_pid, exit_code, error)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(task_id) DO UPDATE SET
                        objective=excluded.objective,
                        command=excluded.command,
                        status=excluded.status,
                        mode=excluded.mode,
                        updated_at=excluded.updated_at,
                        worker_pid=excluded.worker_pid,
                        exit_code=excluded.exit_code,
                        error=excluded.error
                    """,
                    (
                        task_id,
                        objective,
                        command,
                        status,
                        mode,
                        now,
                        now,
                        worker_pid,
                        exit_code,
                        error,
                    ),
                )

        retry_call(_op, attempts=3, delay=0.2, exceptions=(sqlite3.OperationalError,))

    def update_fields(self, task_id: str, **fields) -> None:
        if not fields:
            return
        fields["updated_at"] = time.time()
        assignments = ", ".join(f"{key} = ?" for key in fields)
        values = list(fields.values())
        values.append(task_id)
        def _op() -> None:
            with self._lock, self._connection:
                self._connection.execute(
                    f"UPDATE tasks SET {assignments} WHERE task_id = ?",
                    values,
                )

        retry_call(_op, attempts=3, delay=0.2, exceptions=(sqlite3.OperationalError,))

    def get(self, task_id: str) -> Optional[TaskRow]:
        cursor = self._connection.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,))
        row = cursor.fetchone()
        if not row:
            return None
        return TaskRow(**row)

    def list(self) -> List[TaskRow]:
        cursor = self._connection.execute("SELECT * FROM tasks ORDER BY created_at DESC")
        return [TaskRow(**row) for row in cursor.fetchall()]
