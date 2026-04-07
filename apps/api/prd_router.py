"""PRD Engine API routes.

POST /forgechain/prd              Parse PRD → return dependency graph (no execution yet)
POST /forgechain/prd/{id}/execute Start wave-by-wave execution in background
GET  /forgechain/prd/{id}         Poll overall PRD progress
GET  /forgechain/prd/{id}/graph   Return task dependency graph for UI
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Optional

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, status
from pydantic import BaseModel, Field

import sys
import os
for _p in ["/packages", "../../packages"]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from prd import PRDParser, build_graph, WaveExecutor
from prd.models import TaskGraph

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/forgechain", tags=["prd"])

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class PRDCreateRequest(BaseModel):
    prd: str = Field(..., min_length=50, max_length=20_000, description="Full PRD text")


class PRDTaskOut(BaseModel):
    task_id: str
    title: str
    description: str
    role: str
    dependencies: list[str]
    complexity: str
    wave: int


class ExecutionWaveOut(BaseModel):
    wave_number: int
    task_ids: list[str]


class PRDResponse(BaseModel):
    prd_id: str
    state: str
    current_wave: int
    created_at: float
    updated_at: Optional[float] = None
    critical_path: list[str]
    waves: list[ExecutionWaveOut]
    tasks: list[PRDTaskOut]
    task_job_map: dict[str, str]
    error: Optional[str] = None


class PRDGraphNode(BaseModel):
    task_id: str
    title: str
    role: str
    wave: int
    complexity: str
    is_critical: bool


class PRDGraphEdge(BaseModel):
    from_task: str
    to_task: str


class PRDGraphResponse(BaseModel):
    prd_id: str
    nodes: list[PRDGraphNode]
    edges: list[PRDGraphEdge]
    critical_path: list[str]


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------

def _redis_url() -> str:
    return os.environ["REDIS_URL"]


async def _get_redis() -> aioredis.Redis:
    return aioredis.from_url(_redis_url(), decode_responses=True)


# In-memory store for parsed TaskGraph objects (per-process; survives restarts via Redis)
_graph_cache: dict[str, TaskGraph] = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _load_graph(prd_id: str, redis: aioredis.Redis) -> TaskGraph:
    """Load TaskGraph from in-process cache or rebuild from Redis JSON."""
    if prd_id in _graph_cache:
        return _graph_cache[prd_id]

    raw = await redis.hget(f"forgechain:prd:{prd_id}", "graph_json")
    if raw is None:
        raise HTTPException(status_code=404, detail=f"PRD {prd_id!r} not found")

    data = json.loads(raw)
    graph = _deserialize_graph(prd_id, data)
    _graph_cache[prd_id] = graph
    return graph


def _deserialize_graph(prd_id: str, data: dict) -> TaskGraph:
    from prd.models import PRDTask, ExecutionWave, TaskGraph
    tasks = tuple(
        PRDTask(
            task_id=t["task_id"],
            title=t["title"],
            description=t["description"],
            role=t["role"],  # type: ignore[arg-type]
            dependencies=tuple(t["dependencies"]),
            complexity=t["complexity"],  # type: ignore[arg-type]
            wave=t["wave"],
        )
        for t in data["tasks"]
    )
    waves = tuple(
        ExecutionWave(wave_number=w["wave_number"], task_ids=tuple(w["task_ids"]))
        for w in data["waves"]
    )
    return TaskGraph(
        prd_id=prd_id,
        prd_text=data.get("prd_text", ""),
        tasks=tasks,
        waves=waves,
        critical_path=tuple(data.get("critical_path", [])),
        created_at=float(data.get("created_at", time.time())),
    )


def _serialize_response(graph: TaskGraph, live: dict[str, str]) -> PRDResponse:
    task_job_map = json.loads(live.get("task_job_map", "{}"))
    return PRDResponse(
        prd_id=graph.prd_id,
        state=live.get("state", graph.state),
        current_wave=int(live.get("current_wave", graph.current_wave)),
        created_at=graph.created_at,
        updated_at=float(live.get("updated_at", time.time())),
        critical_path=list(graph.critical_path),
        waves=[
            ExecutionWaveOut(wave_number=w.wave_number, task_ids=list(w.task_ids))
            for w in graph.waves
        ],
        tasks=[
            PRDTaskOut(
                task_id=t.task_id,
                title=t.title,
                description=t.description,
                role=t.role,
                dependencies=list(t.dependencies),
                complexity=t.complexity,
                wave=t.wave,
            )
            for t in graph.tasks
        ],
        task_job_map=task_job_map,
        error=live.get("error"),
    )


async def _run_executor(prd_id: str, graph: TaskGraph, redis_url: str) -> None:
    """Background coroutine — runs wave execution, catches all exceptions."""
    try:
        executor = WaveExecutor(redis_url)
        await executor.execute(graph)
    except Exception:
        logger.exception("[PRD:%s] Background executor crashed", prd_id)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post(
    "/prd",
    response_model=PRDResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Parse PRD and return execution plan (no execution yet)",
)
async def create_prd(
    body: PRDCreateRequest,
    redis: aioredis.Redis = Depends(_get_redis),
) -> PRDResponse:
    """Parse a PRD document into a dependency-sorted task graph.

    Returns the full execution plan — wave assignments and critical path —
    so you can inspect before triggering execution.
    """
    prd_id = str(uuid.uuid4())

    # Parse + build graph (LLM call happens here — may take 5-30s)
    parser = PRDParser()
    tasks = parser.parse(body.prd)
    graph = build_graph(prd_id, body.prd, tasks)

    logger.info(
        "[PRD:%s] Parsed %d tasks across %d waves. Critical path: %s",
        prd_id, len(graph.tasks), len(graph.waves),
        " → ".join(graph.critical_path),
    )

    # Persist to Redis
    key = f"forgechain:prd:{prd_id}"
    graph_data = graph.as_dict()
    await redis.hset(key, mapping={
        "state":        "planned",
        "current_wave": "0",
        "created_at":   str(graph.created_at),
        "updated_at":   str(graph.created_at),
        "task_job_map": "{}",
        "graph_json":   json.dumps(graph_data),
    })
    await redis.expire(key, 60 * 60 * 24 * 7)  # 7-day TTL

    # Cache in-process
    _graph_cache[prd_id] = graph

    live = await redis.hgetall(key)
    return _serialize_response(graph, live)


@router.post(
    "/prd/{prd_id}/execute",
    response_model=PRDResponse,
    summary="Start wave-by-wave execution of a planned PRD",
)
async def execute_prd(
    prd_id: str,
    background_tasks: BackgroundTasks,
    redis: aioredis.Redis = Depends(_get_redis),
) -> PRDResponse:
    """Kick off execution of a PRD that was previously parsed via POST /forgechain/prd.

    Execution runs in the background — use GET /forgechain/prd/{id} to poll progress.
    Workers must be running for tasks to be processed.
    """
    graph = await _load_graph(prd_id, redis)

    live = await redis.hgetall(f"forgechain:prd:{prd_id}")
    current_state = live.get("state", "planned")

    if current_state == "executing":
        raise HTTPException(status_code=409, detail="PRD is already executing")
    if current_state in ("done",):
        raise HTTPException(status_code=409, detail=f"PRD already completed (state={current_state})")

    # Reset state to planned before launching
    await redis.hset(f"forgechain:prd:{prd_id}", mapping={
        "state":        "executing",
        "current_wave": "0",
        "updated_at":   str(time.time()),
        "task_job_map": "{}",
    })

    # Launch background execution
    background_tasks.add_task(_run_executor, prd_id, graph, _redis_url())

    live = await redis.hgetall(f"forgechain:prd:{prd_id}")
    return _serialize_response(graph, live)


@router.get(
    "/prd/{prd_id}",
    response_model=PRDResponse,
    summary="Poll PRD execution progress",
)
async def get_prd(
    prd_id: str,
    redis: aioredis.Redis = Depends(_get_redis),
) -> PRDResponse:
    """Return current state, wave progress, and job mappings for a PRD."""
    graph = await _load_graph(prd_id, redis)
    live = await redis.hgetall(f"forgechain:prd:{prd_id}")
    if not live:
        raise HTTPException(status_code=404, detail=f"PRD {prd_id!r} not found")
    return _serialize_response(graph, live)


@router.get(
    "/prd/{prd_id}/graph",
    response_model=PRDGraphResponse,
    summary="Return dependency graph for UI rendering",
)
async def get_prd_graph(
    prd_id: str,
    redis: aioredis.Redis = Depends(_get_redis),
) -> PRDGraphResponse:
    """Return nodes + directed edges suitable for a graph visualisation library.

    Each node carries wave number and whether it is on the critical path.
    Each edge represents a dependency (from_task must complete before to_task).
    """
    graph = await _load_graph(prd_id, redis)
    critical_set = set(graph.critical_path)

    nodes = [
        PRDGraphNode(
            task_id=t.task_id,
            title=t.title,
            role=t.role,
            wave=t.wave,
            complexity=t.complexity,
            is_critical=t.task_id in critical_set,
        )
        for t in graph.tasks
    ]

    edges = [
        PRDGraphEdge(from_task=dep, to_task=t.task_id)
        for t in graph.tasks
        for dep in t.dependencies
    ]

    return PRDGraphResponse(
        prd_id=prd_id,
        nodes=nodes,
        edges=edges,
        critical_path=list(graph.critical_path),
    )
