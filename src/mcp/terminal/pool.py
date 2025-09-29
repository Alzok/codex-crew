from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from typing import Dict, Optional

from mcp.event_bus import EVENT_BUS
from mcp.terminal.session import TerminalSession


@dataclass
class PooledTerminal:
    session: TerminalSession
    in_use: bool = False


class TerminalPool:
    """Simple terminal pool managing reusable Codex PTY sessions."""

    def __init__(self, size: int) -> None:
        self._size = size
        self._lock = threading.Lock()
        self._pool: Dict[str, PooledTerminal] = {}
        self._available = queue.Queue[TerminalSession]()

    def add(self, session: TerminalSession) -> None:
        with self._lock:
            if session.session_id in self._pool:
                raise ValueError(f"Session {session.session_id} already in pool")
            self._pool[session.session_id] = PooledTerminal(session=session)
            self._available.put(session)
            EVENT_BUS.emit(
                "terminal.pool_added",
                {
                    "session_id": session.session_id,
                },
            )

    def acquire(self, *, block: bool = True, timeout: Optional[float] = None) -> TerminalSession:
        try:
            session = self._available.get(block=block, timeout=timeout)
        except queue.Empty as exc:
            raise TimeoutError("No available terminal session") from exc
        with self._lock:
            record = self._pool.get(session.session_id)
            if not record:
                raise RuntimeError("Pool inconsistency: session missing")
            record.in_use = True
        EVENT_BUS.emit(
            "terminal.pool_acquired",
            {
                "session_id": session.session_id,
            },
        )
        return session

    def release(self, session: TerminalSession) -> None:
        with self._lock:
            record = self._pool.get(session.session_id)
            if not record:
                return
            record.in_use = False
        self._available.put(session)
        EVENT_BUS.emit(
            "terminal.pool_released",
            {
                "session_id": session.session_id,
            },
        )

    def remove(self, session_id: str) -> None:
        with self._lock:
            record = self._pool.pop(session_id, None)
        if record:
            EVENT_BUS.emit(
                "terminal.pool_removed",
                {
                    "session_id": session_id,
                },
            )

    def stats(self) -> Dict[str, object]:
        with self._lock:
            in_use = sum(1 for record in self._pool.values() if record.in_use)
            total = len(self._pool)
        return {
            "size": self._size,
            "pooled": total,
            "in_use": in_use,
            "available": max(total - in_use, 0),
        }

