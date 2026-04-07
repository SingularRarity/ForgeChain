"""PRD Engine data models.

All dataclasses are frozen (immutable) — mutation returns new instances.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal

Role = Literal["frontend_dev", "backend_dev", "qa_backend", "db_eng", "ai_eng", "sre", "ba"]
Complexity = Literal["simple", "moderate", "complex"]
PRDState = Literal["planned", "executing", "done", "failed"]


@dataclass(frozen=True)
class PRDTask:
    """A single atomic task extracted from a PRD."""

    task_id: str
    title: str
    description: str
    role: Role
    dependencies: tuple[str, ...]  # task_ids this depends on
    complexity: Complexity = "moderate"
    wave: int = 0  # assigned by topological sort; 0 = first wave


@dataclass(frozen=True)
class ExecutionWave:
    """A set of tasks that can be executed in parallel."""

    wave_number: int
    task_ids: tuple[str, ...]  # PRDTask.task_id values


@dataclass(frozen=True)
class TaskGraph:
    """Dependency graph + execution plan derived from a PRD."""

    prd_id: str
    prd_text: str
    tasks: tuple[PRDTask, ...]
    waves: tuple[ExecutionWave, ...]
    critical_path: tuple[str, ...]  # ordered task_ids on the longest dependency chain
    created_at: float = field(default_factory=time.time)
    state: PRDState = "planned"
    current_wave: int = 0
    # Mapping: prd_task_id → forgechain job task_id (populated during execution)
    task_job_map: tuple[tuple[str, str], ...] = ()

    def task_by_id(self, task_id: str) -> PRDTask | None:
        for t in self.tasks:
            if t.task_id == task_id:
                return t
        return None

    def as_dict(self) -> dict:
        return {
            "prd_id": self.prd_id,
            "prd_text": self.prd_text[:500] + ("..." if len(self.prd_text) > 500 else ""),
            "state": self.state,
            "current_wave": self.current_wave,
            "created_at": self.created_at,
            "critical_path": list(self.critical_path),
            "waves": [
                {"wave_number": w.wave_number, "task_ids": list(w.task_ids)}
                for w in self.waves
            ],
            "tasks": [
                {
                    "task_id": t.task_id,
                    "title": t.title,
                    "description": t.description,
                    "role": t.role,
                    "dependencies": list(t.dependencies),
                    "complexity": t.complexity,
                    "wave": t.wave,
                }
                for t in self.tasks
            ],
            "task_job_map": dict(self.task_job_map),
        }
