from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from mcp.orchestrator.planner import Plan, PlanTask
from mcp.store import TaskStore
from mcp.terminal.manager import TaskRecord, TerminalManager


@dataclass
class ClaimResult:
    task_id: str
    reads: List[str]
    writes: List[str]
    commands: List[str]
    raw: Dict[str, object]

    @classmethod
    def from_dict(cls, payload: Dict[str, object], *, fallback_task_id: str) -> "ClaimResult":
        resources = payload.get("resources") if isinstance(payload, dict) else {}
        execution = payload.get("execution") if isinstance(payload, dict) else {}
        return cls(
            task_id=str(payload.get("task_id") or fallback_task_id),
            reads=_ensure_str_list(resources.get("reads") if isinstance(resources, dict) else []),
            writes=_ensure_str_list(resources.get("writes") if isinstance(resources, dict) else []),
            commands=_ensure_str_list(execution.get("commands") if isinstance(execution, dict) else []),
            raw=payload,
        )


def _ensure_str_list(value) -> List[str]:  # noqa: ANN001 - valeur dynamique
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(v) for v in value if v is not None]
    return []


class ResourceLocks:
    def __init__(self) -> None:
        self._locks: Dict[str, str] = {}

    def _normalize(self, path: str) -> str:
        return Path(path).as_posix()

    def can_lock(self, task_id: str, paths: List[str]) -> bool:
        for path in paths:
            normalized = self._normalize(path)
            owner = self._locks.get(normalized)
            if owner is not None and owner != task_id:
                return False
        return True

    def acquire(self, task_id: str, paths: List[str]) -> None:
        for path in paths:
            normalized = self._normalize(path)
            self._locks[normalized] = task_id

    def release(self, task_id: str) -> None:
        to_delete = [path for path, owner in self._locks.items() if owner == task_id]
        for path in to_delete:
            del self._locks[path]


class JobRunner:
    def __init__(
        self,
        *,
        job_id: str,
        objective: str,
        job_dir: Path,
        manager: TerminalManager,
        store: TaskStore,
        analysis_timeout: Optional[float] = 120.0,
        execution_timeout: Optional[float] = 600.0,
    ) -> None:
        self.job_id = job_id
        self.objective = objective
        self.job_dir = job_dir
        self.manager = manager
        self.store = store
        self.analysis_timeout = analysis_timeout
        self.execution_timeout = execution_timeout
        self.plan = self._load_plan()
        self.claims: Dict[str, ClaimResult] = {}
        self.completed: set[str] = set()
        self.locks = ResourceLocks()
        self.blocked: set[str] = set()

    def run(self) -> None:
        remaining: Dict[str, PlanTask] = {task.task_id: task for task in self.plan.tasks}
        if not remaining:
            raise RuntimeError("Plan vide : aucune tâche à exécuter")

        while remaining:
            progress = False
            for task_id, task in list(remaining.items()):
                if not self._dependencies_satisfied(task):
                    continue
                claim = self.claims.get(task_id)
                if claim is None:
                    claim = self._analyze_task(task)
                    self.claims[task_id] = claim
                    self._persist_claim(claim)
                    self._log_job_event(
                        "claim_recorded",
                        {
                            "task_id": task_id,
                            "resources": {
                                "reads": claim.reads,
                                "writes": claim.writes,
                            },
                            "commands": claim.commands,
                        },
                    )
                if not self.locks.can_lock(task_id, claim.writes):
                    if task_id not in self.blocked:
                        self.blocked.add(task_id)
                        self.store.update_fields(self.job_id, status=f"blocked:{task_id}")
                        self._log_job_event(
                            "claim_blocked",
                            {
                                "task_id": task_id,
                                "waiting_for": claim.writes,
                            },
                        )
                    continue
                if task_id in self.blocked:
                    self.blocked.remove(task_id)
                    self._log_job_event(
                        "claim_unblocked",
                        {
                            "task_id": task_id,
                        },
                    )
                self.locks.acquire(task_id, claim.writes)
                self._log_job_event(
                    "claim_approved",
                    {
                        "task_id": task_id,
                        "writes": claim.writes,
                    },
                )
                try:
                    self._execute_task(task, claim)
                finally:
                    self.locks.release(task_id)
                    self._log_job_event(
                        "locks_released",
                        {
                            "task_id": task_id,
                            "writes": claim.writes,
                        },
                    )
                self.completed.add(task_id)
                del remaining[task_id]
                progress = True
                break
            if not progress:
                # Aucun progrès possible => soit attente d'une dépendance, soit deadlock
                # On patiente un court instant avant de réévaluer.
                time.sleep(0.5)
                # Si aucune dépendance ne devient disponible après plusieurs tours, on échoue.
                # On peut détecter une impasse si toutes les dépendances sont déjà complétées
                # mais que les verrous bloquent en continu.
                if all(self._dependencies_satisfied(task) for task in remaining.values()):
                    raise RuntimeError("Deadlock : tâches bloquées par des verrous de ressources")

    def _load_plan(self) -> Plan:
        plan_path = self.job_dir / "plan.json"
        if not plan_path.exists():
            raise RuntimeError(f"Plan introuvable pour le job {self.job_id}")
        data = plan_path.read_text(encoding="utf-8")
        return Plan.from_json(data)

    def _dependencies_satisfied(self, task: PlanTask) -> bool:
        return all(dep in self.completed for dep in task.dependencies)

    def _analyze_task(self, task: PlanTask) -> ClaimResult:
        self.store.update_fields(self.job_id, status=f"analysis:{task.task_id}")
        prompt = self._build_claim_prompt(task)
        claim_task_id = f"claim-{self.job_id}-{task.task_id}"
        record = self.manager.create(claim_task_id, prompt, timeout=self.analysis_timeout)
        self._wait(record)
        if record.status != "succeeded":
            raise RuntimeError(f"Analyse échouée pour {task.task_id}: {record.error or record.status}")
        payload = self._extract_json_output(claim_task_id)
        claim = ClaimResult.from_dict(payload, fallback_task_id=task.task_id)
        self.manager.update_metadata(claim_task_id, claim=claim.raw)
        return claim

    def _execute_task(self, task: PlanTask, claim: ClaimResult) -> None:
        self.store.update_fields(self.job_id, status=f"awaiting_exec:{task.task_id}")
        prompt = self._build_execution_prompt(task, claim)
        exec_task_id = f"exec-{self.job_id}-{task.task_id}"
        record = self.manager.create(
            exec_task_id,
            prompt,
            timeout=self.execution_timeout,
            metadata={"claim": claim.raw},
        )
        self._wait(record)
        if record.status != "succeeded":
            self._log_job_event(
                "task_failed",
                {
                    "task_id": task.task_id,
                    "error": record.error,
                    "exit_code": record.exit_code,
                },
            )
            raise RuntimeError(f"Exécution échouée pour {task.task_id}: {record.error or record.status}")
        self.store.update_fields(self.job_id, status=f"executed:{task.task_id}")
        self._log_job_event(
            "task_completed",
            {
                "task_id": task.task_id,
                "writes": claim.writes,
                "commands": claim.commands,
                "stdout_log": str((self.job_dir / exec_task_id / "stdout.log").relative_to(self.job_dir)),
            },
        )

    def _persist_claim(self, claim: ClaimResult) -> None:
        claim_path = self.job_dir / f"{claim.task_id}_claim.json"
        claim_path.write_text(json.dumps(claim.raw, indent=2, ensure_ascii=False), encoding="utf-8")

    def _wait(self, record: TaskRecord) -> None:
        while record.status == "running":
            time.sleep(0.2)

    def _extract_json_output(self, task_id: str) -> Dict[str, object]:
        stdout_text = "".join(self.manager.logs(task_id))
        text = stdout_text.strip()
        if not text:
            raise RuntimeError("Sortie vide pour l'analyse")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start == -1 or end == -1:
                raise RuntimeError("Impossible d'extraire un JSON depuis la sortie Codex")
            snippet = text[start : end + 1]
            return json.loads(snippet)

    def _build_claim_prompt(self, task: PlanTask) -> str:
        return (
            "NUMERUS_CLAIM V1\n"
            f"TASK_ID: {task.task_id}\n"
            f"OBJECTIVE: {self.objective}\n"
            f"SUMMARY: {task.summary}\n"
            f"DESCRIPTION: {task.description}\n"
            "Return JSON ONLY with keys: task_id, resources{reads,writes}, execution{commands}."
        )

    def _build_execution_prompt(self, task: PlanTask, claim: ClaimResult) -> str:
        resources = {
            "reads": claim.reads,
            "writes": claim.writes,
            "commands": claim.commands,
        }
        resources_json = json.dumps(resources, ensure_ascii=False)
        return (
            "NUMERUS_EXECUTE V1\n"
            f"TASK_ID: {task.task_id}\n"
            f"OBJECTIVE: {self.objective}\n"
            f"SUMMARY: {task.summary}\n"
            f"DESCRIPTION: {task.description}\n"
            f"RESOURCES: {resources_json}\n"
            "APPROVAL: GO\n"
            "Effectue la tâche et signale le résultat."
        )

    def _log_job_event(self, event_type: str, payload: Dict[str, object]) -> None:
        event = {
            "ts": time.time(),
            "event": event_type,
            "task_id": payload.get("task_id"),
            "payload": payload,
        }
        path = self.job_dir / "events.ndjson"
        with path.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(event, ensure_ascii=False))
            fp.write("\n")
