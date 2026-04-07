"""PRD Parser — LLM call → structured task list.

Uses the best available LLM provider (prefers Gemini for structured extraction,
falls back through Grok → Ollama). Returns a list of PRDTask with role
assignments and dependency edges.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any

import sys
for _p in ["/packages", "../../packages"]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from providers import get_provider
from .models import PRDTask, Role, Complexity

logger = logging.getLogger(__name__)

_VALID_ROLES: set[str] = {
    "frontend_dev", "backend_dev", "qa_backend",
    "db_eng", "ai_eng", "sre", "ba",
}
_VALID_COMPLEXITY: set[str] = {"simple", "moderate", "complex"}

_SYSTEM_PROMPT = """You are a senior technical architect decomposing a PRD into coding tasks.

Output ONLY a JSON array. No markdown fences, no explanation, no extra text.
Each element must have these exact fields:
{
  "id":           "t1",           // short id, e.g. t1, t2, t3
  "title":        "...",          // ≤10 words
  "description":  "...",          // 1-3 sentences of what to implement
  "role":         "backend_dev",  // MUST be one of: frontend_dev, backend_dev, qa_backend, db_eng, ai_eng, sre, ba
  "dependencies": ["t1"],         // list of ids this task depends on; [] for no deps
  "complexity":   "moderate"      // MUST be one of: simple, moderate, complex
}

Rules:
- db_eng handles schema migrations and database models.
- backend_dev handles API endpoints and business logic.
- frontend_dev handles React components and pages.
- qa_backend handles test suites and coverage.
- sre handles Docker, CI/CD, infra.
- ai_eng handles ML pipelines, embeddings, model integrations.
- ba handles requirements, user stories, acceptance criteria.
- A task must list ONLY ids of tasks it directly depends on.
- No circular dependencies.
- Prefer many small tasks over few large ones (one logical unit per task).
"""


class PRDParser:
    """Parse a PRD text into a list of PRDTask objects."""

    def __init__(self) -> None:
        # Prefer Gemini (best at structured JSON extraction); factory falls back automatically
        self._provider = get_provider("gemini") if _has_gemini() else get_provider()

    def parse(self, prd_text: str) -> list[PRDTask]:
        """Call LLM and return a list of PRDTask.

        Falls back to a single-task plan if LLM returns unparseable output.
        """
        user_prompt = f"PRD:\n\n{prd_text.strip()}"

        try:
            response = self._provider.complete(
                system=_SYSTEM_PROMPT,
                user=user_prompt,
            )
            raw_tasks = _parse_json(response.text)
            if not raw_tasks:
                raise ValueError("LLM returned empty task list")
            return _build_tasks(raw_tasks)
        except Exception as exc:
            logger.warning("PRD parser LLM failed (%s) — using single-task fallback", exc)
            return _fallback_plan(prd_text)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _has_gemini() -> bool:
    import os
    return bool(os.getenv("GEMINI_API_KEY"))


def _parse_json(text: str) -> list[dict[str, Any]]:
    """Extract and parse the first JSON array found in LLM output."""
    # Strip markdown fences if present
    text = re.sub(r"```(?:json)?\s*", "", text).strip()

    # Find first [...] block
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if match:
        text = match.group(0)

    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError(f"Expected JSON array, got {type(data).__name__}")
    return data


def _build_tasks(raw: list[dict[str, Any]]) -> list[PRDTask]:
    """Validate and convert raw dicts into PRDTask objects."""
    # First pass: collect all valid ids
    all_ids = {str(item.get("id", "")).strip() for item in raw}

    tasks: list[PRDTask] = []
    for item in raw:
        task_id = str(item.get("id", uuid.uuid4().hex[:6])).strip()
        role_raw = str(item.get("role", "backend_dev")).strip().lower()
        role: Role = role_raw if role_raw in _VALID_ROLES else "backend_dev"  # type: ignore[assignment]

        complexity_raw = str(item.get("complexity", "moderate")).strip().lower()
        complexity: Complexity = complexity_raw if complexity_raw in _VALID_COMPLEXITY else "moderate"  # type: ignore[assignment]

        # Only keep deps that reference real task ids (prevents dangling edges)
        raw_deps = item.get("dependencies", []) or []
        deps = tuple(
            str(d).strip() for d in raw_deps
            if str(d).strip() in all_ids and str(d).strip() != task_id
        )

        tasks.append(PRDTask(
            task_id=task_id,
            title=str(item.get("title", task_id))[:120],
            description=str(item.get("description", ""))[:1000],
            role=role,
            dependencies=deps,
            complexity=complexity,
        ))

    return tasks


def _fallback_plan(prd_text: str) -> list[PRDTask]:
    """Return a minimal single-task plan when parsing fails."""
    return [
        PRDTask(
            task_id="t1",
            title="Implement PRD requirements",
            description=prd_text[:400],
            role="backend_dev",
            dependencies=(),
            complexity="complex",
        )
    ]
