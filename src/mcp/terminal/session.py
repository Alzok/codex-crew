from __future__ import annotations

import os
import pty
import selectors
import subprocess
import threading
from pathlib import Path
from typing import Dict, Optional

from mcp.event_bus import EVENT_BUS


class TerminalSession:
    """Reusable PTY session wrapping Codex CLI invocations."""

    def __init__(
        self,
        *,
        session_id: str,
        codex_bin: str,
        workdir: Path,
        env: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
    ) -> None:
        self.session_id = session_id
        self.codex_bin = codex_bin
        self.workdir = workdir
        self.env = env or {}
        self.timeout = timeout
        self._master_fd: Optional[int] = None
        self._slave_fd: Optional[int] = None
        self.process: Optional[subprocess.Popen[str]] = None
        self._lock = threading.RLock()

    def configure(
        self,
        *,
        codex_bin: Optional[str] = None,
        workdir: Optional[Path] = None,
        env: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
    ) -> None:
        with self._lock:
            if codex_bin is not None:
                self.codex_bin = codex_bin
            if workdir is not None:
                self.workdir = workdir
            if env is not None:
                self.env = env
            if timeout is not None:
                self.timeout = timeout

    def open(self) -> None:
        with self._lock:
            if self._master_fd is not None:
                return
            master_fd, slave_fd = pty.openpty()
            self._master_fd = master_fd
            self._slave_fd = slave_fd
        EVENT_BUS.emit(
            "terminal.session_opened",
            {
                "session_id": self.session_id,
                "workdir": str(self.workdir),
            },
        )

    def close(self) -> None:
        with self._lock:
            if self._master_fd is not None:
                try:
                    os.close(self._master_fd)
                except OSError:
                    pass
                self._master_fd = None
            if self._slave_fd is not None:
                try:
                    os.close(self._slave_fd)
                except OSError:
                    pass
                self._slave_fd = None
            self.process = None
        EVENT_BUS.emit(
            "terminal.session_closed",
            {
                "session_id": self.session_id,
            },
        )

    def spawn_exec(self, command: str) -> subprocess.Popen[str]:
        with self._lock:
            if self.process and self.process.poll() is None:
                raise RuntimeError("Session already running a process")
            if self._master_fd is None or self._slave_fd is None:
                self.open()
            env = os.environ.copy()
            env.update(self.env)
            process = subprocess.Popen(
                [self.codex_bin, "exec", command],
                stdin=self._slave_fd,
                stdout=self._slave_fd,
                stderr=self._slave_fd,
                cwd=str(self.workdir),
                env=env,
                text=True,
                close_fds=True,
            )
            os.close(self._slave_fd)
            self._slave_fd = None
            self.process = process
        EVENT_BUS.emit(
            "terminal.session_spawn",
            {
                "session_id": self.session_id,
                "pid": process.pid,
                "command": command,
            },
        )
        return process

    def read(self, *, timeout: float = 0.2) -> str:
        with self._lock:
            if self._master_fd is None:
                return ""
            selector = selectors.DefaultSelector()
            selector.register(self._master_fd, selectors.EVENT_READ)
            data = ""
            try:
                ready = selector.select(timeout)
                if ready:
                    chunk = os.read(self._master_fd, 4096)
                    if chunk:
                        data = chunk.decode("utf-8", errors="replace")
            finally:
                selector.unregister(self._master_fd)
            return data

    def write(self, data: str) -> None:
        with self._lock:
            if self._master_fd is None:
                raise RuntimeError("Session not available")
            os.write(self._master_fd, data.encode("utf-8"))

    @property
    def master_fd(self) -> int:
        if self._master_fd is None:
            raise RuntimeError("Session not opened")
        return self._master_fd
