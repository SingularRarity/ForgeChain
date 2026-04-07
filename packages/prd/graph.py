"""Task dependency graph — topological sort, wave assignment, critical path.

Uses Kahn's algorithm for topological sort (O(V+E)).
Uses dynamic programming on the DAG for critical path analysis.
"""

from __future__ import annotations

import uuid
from collections import defaultdict, deque

from .models import PRDTask, ExecutionWave, TaskGraph


def build_graph(prd_id: str, prd_text: str, tasks: list[PRDTask]) -> TaskGraph:
    """Given a flat list of PRDTask, return a fully assembled TaskGraph.

    Steps:
    1. Validate dependency edges (remove any that reference unknown task_ids).
    2. Topological sort (Kahn's algorithm) → detect cycles.
    3. Assign wave numbers (wave = longest path from any root to this node).
    4. Group tasks into ExecutionWave batches.
    5. Compute critical path (longest dependency chain by task count).
    """
    task_map = {t.task_id: t for t in tasks}

    # ------------------------------------------------------------------
    # 1. Build adjacency structures
    # ------------------------------------------------------------------
    # successors[id] = list of task_ids that depend on id
    successors: dict[str, list[str]] = defaultdict(list)
    # in_degree[id] = number of unresolved dependencies
    in_degree: dict[str, int] = {t.task_id: 0 for t in tasks}

    for task in tasks:
        for dep in task.dependencies:
            if dep in task_map:
                successors[dep].append(task.task_id)
                in_degree[task.task_id] += 1

    # ------------------------------------------------------------------
    # 2. Kahn's topological sort + wave assignment
    # ------------------------------------------------------------------
    # wave_level[id] = depth (0 = no dependencies)
    wave_level: dict[str, int] = {}
    queue: deque[str] = deque(
        t.task_id for t in tasks if in_degree[t.task_id] == 0
    )
    for tid in queue:
        wave_level[tid] = 0

    topo_order: list[str] = []

    while queue:
        tid = queue.popleft()
        topo_order.append(tid)
        for successor in successors[tid]:
            in_degree[successor] -= 1
            wave_level[successor] = max(
                wave_level.get(successor, 0),
                wave_level[tid] + 1,
            )
            if in_degree[successor] == 0:
                queue.append(successor)

    if len(topo_order) != len(tasks):
        # Cycle detected — break it by treating remaining tasks as independent
        remaining = [t.task_id for t in tasks if t.task_id not in wave_level]
        max_wave = max(wave_level.values(), default=0) + 1
        for tid in remaining:
            wave_level[tid] = max_wave
            topo_order.append(tid)

    # ------------------------------------------------------------------
    # 3. Rebuild tasks with wave assignments (returns new frozen instances)
    # ------------------------------------------------------------------
    reassigned = tuple(
        PRDTask(
            task_id=t.task_id,
            title=t.title,
            description=t.description,
            role=t.role,
            dependencies=t.dependencies,
            complexity=t.complexity,
            wave=wave_level.get(t.task_id, 0),
        )
        for t in tasks
    )

    # ------------------------------------------------------------------
    # 4. Group into ExecutionWave objects
    # ------------------------------------------------------------------
    max_wave_num = max((t.wave for t in reassigned), default=0)
    waves = tuple(
        ExecutionWave(
            wave_number=w,
            task_ids=tuple(t.task_id for t in reassigned if t.wave == w),
        )
        for w in range(max_wave_num + 1)
        if any(t.wave == w for t in reassigned)
    )

    # ------------------------------------------------------------------
    # 5. Critical path — longest chain (by number of tasks)
    # ------------------------------------------------------------------
    critical_path = _compute_critical_path(reassigned, topo_order, successors)

    return TaskGraph(
        prd_id=prd_id,
        prd_text=prd_text,
        tasks=reassigned,
        waves=waves,
        critical_path=tuple(critical_path),
    )


def _compute_critical_path(
    tasks: tuple[PRDTask, ...],
    topo_order: list[str],
    successors: dict[str, list[str]],
) -> list[str]:
    """Return ordered list of task_ids on the longest dependency chain.

    Uses DP: dist[v] = longest path ending at v (in number of tasks).
    We then backtrack from the node with the maximum dist.
    """
    task_map = {t.task_id: t for t in tasks}
    dist: dict[str, int] = {tid: 1 for tid in topo_order}
    prev: dict[str, str | None] = {tid: None for tid in topo_order}

    for tid in topo_order:
        for successor in successors.get(tid, []):
            if dist[tid] + 1 > dist.get(successor, 1):
                dist[successor] = dist[tid] + 1
                prev[successor] = tid

    if not dist:
        return []

    # Find end of critical path
    end = max(dist, key=lambda k: dist[k])

    # Backtrack to reconstruct path
    path: list[str] = []
    current: str | None = end
    while current is not None:
        path.append(current)
        current = prev.get(current)

    path.reverse()
    return path
