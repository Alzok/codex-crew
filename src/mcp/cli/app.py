from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import List

from mcp.event_bus import EVENT_BUS
from mcp.orchestrator.planner import CodexPlanner, PlanError
from mcp.orchestrator.roles import RolePlanner
from mcp.store import TaskStore
from mcp.terminal.manager import TerminalManager


def _runs_dir() -> Path:
    return Path(os.environ.get("MCP_RUNS_DIR", "runs"))


def _store_path() -> Path:
    return Path(os.environ.get("MCP_STORE_PATH", "store/tasks.db")).resolve()


def _launch_task(objective: str, max_parallel: int | None) -> int:
    del max_parallel  # placeholder until scheduler implémente le parallélisme
    store_path = _store_path()
    os.environ["MCP_STORE_PATH"] = str(store_path)
    store = TaskStore()
    task_id = uuid.uuid4().hex[:8]
    runs_root = _runs_dir()
    job_dir = runs_root / task_id
    job_dir.mkdir(parents=True, exist_ok=True)

    codex_bin = os.environ.get("CODEX_BIN", "codex")
    planner_manager = TerminalManager(runs_dir=runs_root, codex_bin=codex_bin)
    planner = CodexPlanner(planner_manager)
    try:
        plan = planner.generate_plan(objective=objective, job_id=task_id)
    except PlanError as exc:
        print(f"Échec de la planification : {exc}", file=sys.stderr)
        return 1

    role_planner = RolePlanner(planner_manager)
    assignments = role_planner.assign(plan, job_id=task_id)
    for task in plan.tasks:
        if task.task_id in assignments:
            task.role = assignments[task.task_id].role

    plan_path = job_dir / "plan.json"
    plan_path.write_text(plan.to_json(), encoding="utf-8")
    print(f"Plan ({len(plan.tasks)} tâche(s)) → {plan_path}")
    for task in plan.tasks:
        print(f"  - {task.task_id} [{task.role}]: {task.summary}")
    EVENT_BUS.emit(
        "job.plan_created",
        {
            "job_id": task_id,
            "objective": objective,
            "plan_path": str(plan_path),
            "tasks": [task.to_dict() for task in plan.tasks],
        },
    )

    store.upsert_task(
        task_id=task_id,
        objective=objective,
        command=objective,
        status="pending",
        mode="exec",
    )

    env = os.environ.copy()
    env.setdefault("MCP_RUNS_DIR", str(runs_root.resolve()))
    env.setdefault("MCP_STORE_PATH", str(store_path))
    env.setdefault("CODEX_BIN", codex_bin)
    worker_cmd: List[str] = [sys.executable, "-m", "mcp.orchestrator.worker", task_id]
    process = subprocess.Popen(worker_cmd, env=env, start_new_session=True)
    store.update_fields(task_id, worker_pid=process.pid, status="running")
    print(f"task {task_id} started")
    EVENT_BUS.emit(
        "job.started",
        {
            "job_id": task_id,
            "objective": objective,
            "plan_path": str(plan_path),
            "worker_pid": process.pid,
        },
    )
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    return _launch_task(args.objective, args.max_parallel)


def cmd_start(args: argparse.Namespace) -> int:
    objective = args.objective or ""
    if not objective:
        try:
            objective = input("Numerus › objectif : ").strip()
        except EOFError:
            objective = ""
    if not objective:
        print("Aucun objectif fourni", file=sys.stderr)
        return 1
    print(f"Planification en cours pour : {objective}")
    return _launch_task(objective, args.max_parallel)


def cmd_status(args: argparse.Namespace) -> int:
    os.environ.setdefault("MCP_STORE_PATH", str(_store_path()))
    store = TaskStore()
    runs_dir = _runs_dir()

    rows = store.list()
    header = f"{'Task':<12} {'Status':<12} {'Created':<10} {'Updated':<10} {'PID':<8} {'Exit':<6} Workdir"
    print(header)
    print("-" * len(header))
    for row in rows:
        created = time.strftime("%H:%M:%S", time.localtime(row.created_at))
        updated = time.strftime("%H:%M:%S", time.localtime(row.updated_at))
        pid = str(row.worker_pid or "-")
        exit_code = "-" if row.exit_code is None else str(row.exit_code)
        workdir = runs_dir / row.task_id
        print(
            f"{row.task_id:<12} {row.status:<12} {created:<10} {updated:<10} {pid:<8} {exit_code:<6} {workdir}",
        )
    return 0


def cmd_logs(args: argparse.Namespace) -> int:
    task_id = args.task_id
    runs_dir = _runs_dir()
    stdout_path = runs_dir / task_id / "stdout.log"
    if not stdout_path.exists():
        print(f"No logs for task {task_id}", file=sys.stderr)
        return 1

    if args.follow:
        print(f"--- tailing {stdout_path} ---")
        with stdout_path.open("r", encoding="utf-8") as fp:
            fp.seek(0, os.SEEK_END)
            try:
                while True:
                    line = fp.readline()
                    if line:
                        print(line.rstrip("\n"))
                    else:
                        time.sleep(0.5)
            except KeyboardInterrupt:
                return 0
    else:
        print(stdout_path.read_text(encoding="utf-8"))
    return 0


def cmd_kill(args: argparse.Namespace) -> int:
    os.environ.setdefault("MCP_STORE_PATH", str(_store_path()))
    store = TaskStore()
    row = store.get(args.task_id)
    if not row:
        print(f"Task {args.task_id} not found", file=sys.stderr)
        return 1
    if not row.worker_pid:
        print(f"Task {args.task_id} has no worker pid", file=sys.stderr)
        return 1
    try:
        os.kill(row.worker_pid, signal.SIGTERM)
        store.update_fields(args.task_id, status="terminating")
        print(f"Sent SIGTERM to task {args.task_id} (pid {row.worker_pid})")
        return 0
    except ProcessLookupError:
        print(f"Worker process {row.worker_pid} missing", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Supervisor CLI for Codex MCP orchestrator")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Create a new task")
    run_parser.add_argument("objective", help="Objective/prompt for Codex")
    run_parser.add_argument("--max-parallel", type=int, default=None, help="Planner parallelism cap")
    run_parser.set_defaults(func=cmd_run)

    start_parser = subparsers.add_parser("start", help="Mode interactif : saisie de l'objectif et lancement")
    start_parser.add_argument("--objective", "-o", help="Objectif initial")
    start_parser.add_argument("--max-parallel", type=int, default=None, help="Planner parallelism cap")
    start_parser.set_defaults(func=cmd_start)

    status_parser = subparsers.add_parser("status", help="Show all tasks")
    status_parser.set_defaults(func=cmd_status)

    logs_parser = subparsers.add_parser("logs", help="Show logs for a task")
    logs_parser.add_argument("task_id")
    logs_parser.add_argument("--follow", action="store_true", help="Stream logs")
    logs_parser.set_defaults(func=cmd_logs)

    kill_parser = subparsers.add_parser("kill", help="Terminate a running task")
    kill_parser.add_argument("task_id")
    kill_parser.set_defaults(func=cmd_kill)

    return parser


def main(argv: List[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover - manual usage
    sys.exit(main())
