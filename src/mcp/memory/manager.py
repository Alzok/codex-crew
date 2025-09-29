from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from mcp.event_bus import EVENT_BUS
from mcp.utils import retry_call


@dataclass
class MemoryEntry:
    entry_id: str
    bank_id: str
    entry_type: str
    data: Dict[str, object]
    created_at: float


class MemoryManager:
    """SQLite-backed memory storage with simple in-memory cache."""

    def __init__(self, db_path: Path | str = "store/memory.db", cache_size: int = 128) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._cache_size = cache_size
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self._db_path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._ensure_schema()
        self._cache: Dict[str, MemoryEntry] = {}

    def _ensure_schema(self) -> None:
        with self._connection:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS banks (
                    bank_id TEXT PRIMARY KEY,
                    label TEXT,
                    created_at REAL NOT NULL
                )
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS entries (
                    entry_id TEXT PRIMARY KEY,
                    bank_id TEXT NOT NULL,
                    entry_type TEXT NOT NULL,
                    data TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    FOREIGN KEY(bank_id) REFERENCES banks(bank_id)
                )
                """
            )

    def ensure_bank(self, label: str) -> str:
        bank_id = f"bank-{uuid.uuid4().hex[:8]}"
        def _op() -> None:
            with self._lock, self._connection:
                self._connection.execute(
                    "INSERT INTO banks (bank_id, label, created_at) VALUES (?, ?, ?)",
                    (bank_id, label, time.time()),
                )

        retry_call(_op, attempts=3, delay=0.2, exceptions=(sqlite3.OperationalError,))
        EVENT_BUS.emit(
            "memory.bank_created",
            {
                "bank_id": bank_id,
                "label": label,
            },
        )
        return bank_id

    def store(
        self,
        *,
        bank_id: str,
        entry_type: str,
        data: Dict[str, object],
    ) -> MemoryEntry:
        entry = MemoryEntry(
            entry_id=f"mem-{uuid.uuid4().hex[:8]}",
            bank_id=bank_id,
            entry_type=entry_type,
            data=data,
            created_at=time.time(),
        )
        payload = json.dumps(entry.data, ensure_ascii=False)
        def _op() -> None:
            with self._lock, self._connection:
                self._connection.execute(
                    "INSERT INTO entries (entry_id, bank_id, entry_type, data, created_at) VALUES (?, ?, ?, ?, ?)",
                    (entry.entry_id, entry.bank_id, entry.entry_type, payload, entry.created_at),
                )

        retry_call(_op, attempts=3, delay=0.2, exceptions=(sqlite3.OperationalError,))
        self._add_cache(entry)
        EVENT_BUS.emit(
            "memory.entry_added",
            {
                "bank_id": bank_id,
                "entry_id": entry.entry_id,
                "entry_type": entry.entry_type,
            },
        )
        return entry

    def list_entries(
        self,
        *,
        bank_id: str,
        entry_type: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[MemoryEntry]:
        sql = "SELECT * FROM entries WHERE bank_id = ?"
        params: List[object] = [bank_id]
        if entry_type:
            sql += " AND entry_type = ?"
            params.append(entry_type)
        sql += " ORDER BY created_at DESC"
        if limit:
            sql += " LIMIT ?"
            params.append(limit)
        with self._lock:
            rows = self._connection.execute(sql, params).fetchall()
        entries = [
            MemoryEntry(
                entry_id=row["entry_id"],
                bank_id=row["bank_id"],
                entry_type=row["entry_type"],
                data=json.loads(row["data"]),
                created_at=row["created_at"],
            )
            for row in rows
        ]
        return entries

    def _add_cache(self, entry: MemoryEntry) -> None:
        self._cache[entry.entry_id] = entry
        if len(self._cache) > self._cache_size:
            # Remove oldest entry based on created_at
            oldest = min(self._cache.values(), key=lambda e: e.created_at)
            self._cache.pop(oldest.entry_id, None)


MEMORY_MANAGER = MemoryManager()
