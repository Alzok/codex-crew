from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, Optional

from mcp.event_bus import EVENT_BUS
from mcp.terminal.pool import TerminalPool
from mcp.terminal.session import TerminalSession
from mcp.utils import CircuitBreaker, CircuitBreakerOpen, retry_call


@dataclass
class TaskRecord:
    task_id: str
    workdir: Path
    status: str = "running"
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None
    exit_code: Optional[int] = None
    mode: str = "exec"
    command: str = ""
    error: Optional[str] = None
    metadata: Dict[str, object] = field(default_factory=dict)

    @property
    def duration(self) -> Optional[float]:
        if self.end_time is None:
            return None
        return self.end_time - self.start_time


class TerminalManager:
    """Manage Codex CLI executions inside reusable PTY sessions."""

    def __init__(self, runs_dir: Path | str = "runs", codex_bin: str = "codex", pool_size: int = 4) -> None:
        self._runs_dir = Path(runs_dir)
        self._runs_dir.mkdir(parents=True, exist_ok=True)
        self._codex_bin = codex_bin
        self._tasks: Dict[str, TaskRecord] = {}
        self._processes: Dict[str, TerminalSession] = {}
        self._lock = threading.Lock()
        self._pool = TerminalPool(size=pool_size)
        self._spawn_breaker = CircuitBreaker("terminal_spawn", threshold=3, cooldown=30.0)

    def create(
        self,
        task_id: str,
        command: str,
        *,
        mode: str = "exec",
        env: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
        metadata: Optional[Dict[str, object]] = None,
    ) -> TaskRecord:
        if mode not in {"exec"}:
            raise ValueError(f"Unsupported mode: {mode}")

        with self._lock:
            if task_id in self._tasks:
                raise ValueError(f"Task {task_id} already exists")

        workdir = self._runs_dir / task_id
        workdir.mkdir(parents=True, exist_ok=True)
        stdout_path = workdir / "stdout.log"
        events_path = workdir / "events.ndjson"

        task = TaskRecord(
            task_id=task_id,
            workdir=workdir,
            mode=mode,
            command=command,
            metadata=dict(metadata or {}),
        )

        env_vars = os.environ.copy()
        if env:
            env_vars.update(env)

        session = self._checkout_session(workdir, env_vars, timeout)
        try:
            self._spawn_breaker.allow()
            process = retry_call(
                lambda: session.spawn_exec(command),
                attempts=3,
                delay=0.5,
            )
        except CircuitBreakerOpen as exc:
            self._pool.release(session)
            raise RuntimeError("Terminal spawn temporairement indisponible") from exc
        except Exception:
            self._spawn_breaker.record_failure()
            session.close()
            self._pool.remove(session.session_id)
            raise
        else:
            self._spawn_breaker.record_success()

        with self._lock:
            self._tasks[task_id] = task
            self._processes[task_id] = session

        self._write_event(
            events_path,
            "started",
            {
                "pid": process.pid,
                "session_id": session.session_id,
                "command": [self._codex_bin, "exec", command],
                "metadata": task.metadata,
            },
        )

        watcher = threading.Thread(
            target=self._watch_process,
            args=(task, session, process, stdout_path, events_path, timeout),
            daemon=True,
        )
        watcher.start()

        return task

    def _checkout_session(
        self,
        workdir: Path,
        env: Dict[str, str],
        timeout: Optional[float],
    ) -> TerminalSession:
        try:
            session = self._pool.acquire(block=False)
        except TimeoutError:
            session = TerminalSession(
                session_id=f"session-{uuid.uuid4().hex[:8]}",
                codex_bin=self._codex_bin,
                workdir=workdir,
                env=env,
                timeout=timeout,
            )
            session.open()
            self._pool.add(session)
            session = self._pool.acquire(block=False)
        session.configure(codex_bin=self._codex_bin, workdir=workdir, env=env, timeout=timeout)
        return session

    def _watch_process(
        self,
        task: TaskRecord,
        session: TerminalSession,
        process: subprocess.Popen[str],
        stdout_path: Path,
        events_path: Path,
        timeout: Optional[float],
    ) -> None:
        deadline = time.time() + timeout if timeout else None
        timed_out = False

        with stdout_path.open("a", encoding="utf-8") as stdout_file:
            while True:
                if deadline and time.time() > deadline:
                    timed_out = True
                    if process.poll() is None:
                        process.terminate()
                    self._write_event(events_path, "timeout", {"timeout": timeout})
                    break
                chunk = session.read(timeout=0.2)
                if chunk:
                    stdout_file.write(chunk)
                    stdout_file.flush()
                    self._write_event(events_path, "stdout", {"data": chunk})
                if process.poll() is not None and not chunk:
                    break

        exit_status = process.wait()
        session.close()
        self._pool.release(session)

        task.exit_code = exit_status
        task.end_time = time.time()

        if task.error == "killed":
            task.status = "failed"
        elif timed_out:
            task.status = "failed"
            task.error = "timeout"
        elif exit_status == 0:
            task.status = "succeeded"
            task.error = None
        else:
            task.status = "failed"
            task.error = f"exit_code={exit_status}"

        self._write_event(events_path, "exit", {"exit_code": exit_status})

        with self._lock:
            self._processes.pop(task.task_id, None)
            self._tasks[task.task_id] = task

    def _write_event(self, path: Path, event_type: str, payload: Dict[str, object]) -> None:
        event = {
            "ts": time.time(),
            "type": event_type,
            "payload": payload,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(event, ensure_ascii=False))
            fp.write("\n")
        EVENT_BUS.emit(
            f"terminal.{event_type}",
            {
                "task_path": str(path.parent),
                **payload,
            },
        )

    def logs(self, task_id: str) -> Iterable[str]:
        task = self._tasks.get(task_id)
        if not task:
            raise KeyError(f"Unknown task {task_id}")
        stdout_path = task.workdir / "stdout.log"
        if not stdout_path.exists():
            return []
        with stdout_path.open("r", encoding="utf-8") as fp:
            return fp.readlines()

    def kill(self, task_id: str) -> None:
        with self._lock:
            session = self._processes.get(task_id)
            task = self._tasks.get(task_id)
        if not session or not task or not session.process:
            return
        session.process.terminate()
        session.close()
        self._pool.remove(session.session_id)
        task.status = "failed"
        task.error = "killed"
        task.end_time = time.time()
        self._write_event(task.workdir / "events.ndjson", "killed", {"signal": "SIGTERM"})

    def status(self, task_id: str) -> TaskRecord:
        task = self._tasks.get(task_id)
        if not task:
            raise KeyError(f"Unknown task {task_id}")
        return task

    def list(self) -> Dict[str, TaskRecord]:
        with self._lock:
            return dict(self._tasks)

    def update_metadata(self, task_id: str, **fields: object) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                raise KeyError(f"Unknown task {task_id}")
            task.metadata.update(fields)
        self._write_event(
            task.workdir / "events.ndjson",
            "metadata",
            {"updates": fields},
        )

    def attach(self, task_id: str):
        with self._lock:
            session = self._processes.get(task_id)
        if not session:
            raise KeyError(f"Task {task_id} not attachable")
        dup_fd = os.dup(session.master_fd)
        return os.fdopen(dup_fd, "r+", encoding="utf-8", buffering=1)

    def send(self, task_id: str, data: str) -> None:
        with self._lock:
            session = self._processes.get(task_id)
        if not session:
            raise KeyError(f"Unknown task {task_id}")
        session.write(data)
