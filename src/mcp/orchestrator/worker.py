from __future__ import annotations

import os
import signal
import sys
from pathlib import Path

from mcp.store import TaskStore
from mcp.orchestrator.job_runner import JobRunner
from mcp.terminal.manager import TerminalManager

_runs_dir = Path(os.environ.get("MCP_RUNS_DIR", "runs"))
_codex_bin = os.environ.get("CODEX_BIN", "codex")

_current_manager: TerminalManager | None = None
_current_task_id: str | None = None


def _handle_sigterm(signum: int, frame) -> None:  # type: ignore[annotation-unchecked]
    del signum, frame
    if _current_manager and _current_task_id:
        _current_manager.kill(_current_task_id)
    sys.exit(0)


def main(task_id: str) -> int:
    global _current_manager, _current_task_id

    store = TaskStore()
    row = store.get(task_id)
    if not row:
        print(f"Task {task_id} not found", file=sys.stderr)
        return 1

    job_dir = _runs_dir / task_id
    job_dir.mkdir(parents=True, exist_ok=True)
    manager = TerminalManager(runs_dir=job_dir, codex_bin=_codex_bin)
    _current_manager = manager
    _current_task_id = task_id

    signal.signal(signal.SIGTERM, _handle_sigterm)

    try:
        store.update_fields(task_id, status="running")
        runner = JobRunner(
            job_id=task_id,
            objective=row.objective,
            job_dir=job_dir,
            manager=manager,
            store=store,
        )
        runner.run()
        store.update_fields(task_id, status="succeeded", exit_code=0, error=None)
        return 0
    except Exception as exc:  # noqa: BLE001
        store.update_fields(task_id, status="failed", error=str(exc))
        raise


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python -m mcp.orchestrator.worker <task_id>", file=sys.stderr)
        sys.exit(1)
    sys.exit(main(sys.argv[1]))
