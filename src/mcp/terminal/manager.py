from __future__ import annotations

import json
import os
import selectors
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, Optional


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
    """Manage Codex CLI executions inside dedicated PTYs."""

    def __init__(self, runs_dir: Path | str = "runs", codex_bin: str = "codex") -> None:
        self._runs_dir = Path(runs_dir)
        self._runs_dir.mkdir(parents=True, exist_ok=True)
        self._codex_bin = codex_bin
        self._tasks: Dict[str, TaskRecord] = {}
        self._processes: Dict[str, subprocess.Popen[str]] = {}
        self._masters: Dict[str, int] = {}
        self._lock = threading.Lock()

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

        spawn_command = [self._codex_bin, "exec", command]

        master_fd, slave_fd = os.openpty()
        process = subprocess.Popen(
            spawn_command,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            cwd=str(workdir),
            env=env_vars,
            text=True,
            close_fds=True,
        )
        os.close(slave_fd)

        with self._lock:
            self._tasks[task_id] = task
            self._processes[task_id] = process
            self._masters[task_id] = master_fd

        self._write_event(events_path, "started", {"pid": process.pid, "command": spawn_command, "metadata": task.metadata})

        watcher = threading.Thread(
            target=self._watch_process,
            args=(task, process, master_fd, stdout_path, events_path, timeout),
            daemon=True,
        )
        watcher.start()

        return task

    def _watch_process(
        self,
        task: TaskRecord,
        process: subprocess.Popen[str],
        master_fd: int,
        stdout_path: Path,
        events_path: Path,
        timeout: Optional[float],
    ) -> None:
        deadline = time.time() + timeout if timeout else None
        timed_out = False
        selector = selectors.DefaultSelector()
        selector.register(master_fd, selectors.EVENT_READ)

        try:
            with stdout_path.open("a", encoding="utf-8") as stdout_file:
                while True:
                    if deadline and time.time() > deadline:
                        timed_out = True
                        process.terminate()
                        self._write_event(events_path, "timeout", {"timeout": timeout})
                        break

                    ready = selector.select(timeout=0.2)
                    if ready:
                        for _key, _mask in ready:
                            try:
                                data = os.read(master_fd, 4096)
                            except OSError:
                                ready = None
                                break
                            if not data:
                                ready = None
                                break
                            text = data.decode("utf-8", errors="replace")
                            stdout_file.write(text)
                            stdout_file.flush()
                            self._write_event(events_path, "stdout", {"data": text})
                    if process.poll() is not None:
                        break
        finally:
            selector.unregister(master_fd)
            os.close(master_fd)

        exit_status = process.wait()
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
            self._masters.pop(task.task_id, None)

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
            process = self._processes.get(task_id)
            task = self._tasks.get(task_id)
        if not process or not task:
            return
        process.terminate()
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
        """Return a file-like object bound to the PTY master for interactive usage."""

        with self._lock:
            master_fd = self._masters.get(task_id)
            process = self._processes.get(task_id)
        if master_fd is None or process is None:
            raise KeyError(f"Task {task_id} not attachable")
        dup_fd = os.dup(master_fd)
        return os.fdopen(dup_fd, "r+", encoding="utf-8", buffering=1)

    def send(self, task_id: str, data: str) -> None:
        with self._lock:
            master_fd = self._masters.get(task_id)
        if master_fd is None:
            raise KeyError(f"Unknown task {task_id}")
        os.write(master_fd, data.encode("utf-8"))
