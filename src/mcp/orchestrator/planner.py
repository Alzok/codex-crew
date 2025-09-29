from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

from mcp.terminal.manager import TaskRecord, TerminalManager


class PlanError(RuntimeError):
    """Raised when planning fails or produces invalid output."""


@dataclass
class PlanTask:
    task_id: str
    summary: str
    description: str = ""
    dependencies: List[str] = field(default_factory=list)
    reads: List[str] = field(default_factory=list)
    writes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        return {
            "id": self.task_id,
            "summary": self.summary,
            "description": self.description,
            "dependencies": self.dependencies,
            "resources": {
                "reads": self.reads,
                "writes": self.writes,
            },
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, object]) -> "PlanTask":
        task_id = str(payload.get("id") or payload.get("task_id") or uuid.uuid4().hex[:6])
        summary = str(payload.get("summary") or payload.get("title") or "")
        description = str(payload.get("description") or payload.get("details") or "")
        dependencies = cls._ensure_str_list(payload.get("dependencies") or payload.get("requires") or [])
        resources = payload.get("resources") if isinstance(payload.get("resources"), dict) else {}
        reads = cls._ensure_str_list(resources.get("reads") if isinstance(resources, dict) else [])
        writes = cls._ensure_str_list(resources.get("writes") if isinstance(resources, dict) else [])
        if not summary:
            summary = "No summary provided"
        return cls(
            task_id=task_id,
            summary=summary,
            description=description,
            dependencies=dependencies,
            reads=reads,
            writes=writes,
        )

    @staticmethod
    def _ensure_str_list(value) -> List[str]:  # noqa: ANN001 - helper
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return [str(v) for v in value if v is not None]
        return []


@dataclass
class Plan:
    objective: str
    tasks: List[PlanTask]

    def to_dict(self) -> Dict[str, object]:
        return {
            "objective": self.objective,
            "tasks": [task.to_dict() for task in self.tasks],
        }

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    @classmethod
    def from_dict(cls, payload: Dict[str, object]) -> "Plan":
        objective = str(payload.get("objective") or payload.get("goal") or "")
        tasks_data = payload.get("tasks") if isinstance(payload, dict) else []
        tasks: List[PlanTask] = []
        if isinstance(tasks_data, list):
            for item in tasks_data:
                if isinstance(item, dict):
                    tasks.append(PlanTask.from_dict(item))
        return cls(objective=objective, tasks=tasks)

    @classmethod
    def from_json(cls, text: str) -> "Plan":
        return cls.from_dict(json.loads(text))


class CodexPlanner:
    """Generate a task plan through a Codex CLI PTY."""

    PROMPT_TEMPLATE = (
        "NUMERUS_PLAN V1. OBJECTIVE: {objective}. "
        "Return JSON only with schema: "
        "{{\"objective\": string, \"tasks\": [{{\"id\": string, \"summary\": string, \"description\": string, \"dependencies\": [string], \"resources\": {{\"reads\": [string], \"writes\": [string]}}}}]}}. "
        "Use concise ids (kebab-case)."
    )

    def __init__(self, manager: TerminalManager) -> None:
        self._manager = manager

    def generate_plan(
        self,
        *,
        objective: str,
        job_id: str,
        timeout: float | None = 120.0,
    ) -> Plan:
        planner_task_id = f"planner-{job_id}-{uuid.uuid4().hex[:4]}"
        prompt = self.PROMPT_TEMPLATE.format(objective=objective.strip())

        record = self._manager.create(
            planner_task_id,
            prompt,
            mode="exec",
            timeout=timeout,
        )
        self._wait_for_completion(record)

        if record.status != "succeeded":
            raise PlanError(f"Planning failed: {record.error or record.status}")

        stdout = "".join(self._manager.logs(planner_task_id))
        data = self._parse_plan_json(stdout)
        plan = self._build_plan(objective, data)
        return plan

    def _wait_for_completion(self, record: TaskRecord) -> None:
        while record.status == "running":
            time.sleep(0.2)

    def _parse_plan_json(self, raw: str) -> Dict[str, object]:
        text = raw.strip()
        if not text:
            raise PlanError("Aucune sortie du planificateur")

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start == -1 or end == -1:
                raise PlanError("Sortie du planificateur illisible") from None
            snippet = text[start : end + 1]
            try:
                return json.loads(snippet)
            except json.JSONDecodeError as exc:  # pragma: no cover - informationnel
                raise PlanError("JSON de plan invalide") from exc

    def _build_plan(self, objective: str, payload: Dict[str, object]) -> Plan:
        tasks_payload = payload.get("tasks") if isinstance(payload, dict) else None
        if not isinstance(tasks_payload, list) or not tasks_payload:
            raise PlanError("Le plan doit contenir au moins une tâche")

        tasks: List[PlanTask] = []
        for item in tasks_payload:
            if isinstance(item, dict):
                tasks.append(PlanTask.from_dict(item))

        return Plan(objective=objective, tasks=tasks)
