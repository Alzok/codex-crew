from __future__ import annotations

import json
import textwrap
import time
from dataclasses import dataclass
from typing import Dict, List

from mcp.event_bus import EVENT_BUS
from mcp.orchestrator.planner import Plan, PlanTask
from mcp.terminal.manager import TerminalManager

DEFAULT_ROLES = ["queen", "planner", "executor", "reviewer"]


@dataclass
class RoleAssignment:
    task_id: str
    role: str
    notes: str = ""


class RolePlanner:
    PROMPT_TEMPLATE = textwrap.dedent(
        """
        NUMERUS_ROLES V1
        OBJECTIVE: {objective}
        TASKS:
        {tasks}

        Assign a role from the set {roles} to each task.
        Return JSON with schema:
        {{
          "roles": [{{"id": "task-id", "role": "executor", "notes": "optional"}}],
          "strategy": "short guidance"
        }}
        """
    ).strip()

    def __init__(self, manager: TerminalManager) -> None:
        self._manager = manager

    def assign(self, plan: Plan, *, job_id: str, timeout: float | None = 90.0) -> Dict[str, RoleAssignment]:
        tasks_blob = "\n".join(
            f"- {task.task_id}: {task.summary}" for task in plan.tasks
        )
        prompt = self.PROMPT_TEMPLATE.format(
            objective=plan.objective,
            tasks=tasks_blob,
            roles=DEFAULT_ROLES,
        )
        task_id = f"roles-{job_id}"
        record = self._manager.create(task_id, prompt, timeout=timeout)
        while record.status == "running":
            time.sleep(0.2)
        if record.status != "succeeded":
            raise RuntimeError(f"Role planning failed: {record.error or record.status}")
        payload = self._parse(self._manager.logs(task_id))
        assignments = self._to_assignments(payload, plan)
        EVENT_BUS.emit(
            "job.roles_assigned",
            {
                "job_id": job_id,
                "roles": [assignment.__dict__ for assignment in assignments.values()],
                "strategy": payload.get("strategy"),
            },
        )
        return assignments

    def _parse(self, logs: List[str]) -> Dict[str, object]:
        text = "".join(logs).strip()
        if not text:
            raise RuntimeError("Empty role planner output")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start == -1 or end == -1:
                raise
            snippet = text[start : end + 1]
            return json.loads(snippet)

    def _to_assignments(self, payload: Dict[str, object], plan: Plan) -> Dict[str, RoleAssignment]:
        result: Dict[str, RoleAssignment] = {}
        roles = payload.get("roles") if isinstance(payload, dict) else None
        if isinstance(roles, list):
            for entry in roles:
                if not isinstance(entry, dict):
                    continue
                task_id = str(entry.get("id") or "").strip()
                role = str(entry.get("role") or "").strip().lower()
                notes = str(entry.get("notes") or "").strip()
                if task_id and role:
                    result[task_id] = RoleAssignment(task_id=task_id, role=role, notes=notes)
        # Fallback heuristic
        if not result:
            for task in plan.tasks:
                lower = task.summary.lower()
                if any(keyword in lower for keyword in ("plan", "spec", "analysis")):
                    role = "planner"
                elif "review" in lower or "test" in lower:
                    role = "reviewer"
                else:
                    role = "executor"
                result[task.task_id] = RoleAssignment(task_id=task.task_id, role=role)
        return result

