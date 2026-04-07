"""Wave executor — runs PRD task waves in parallel, polling until each wave clears.

Execution model:
  For each wave:
    1. Create a ForgeChain job for every task in the wave (via Redis state machine).
    2. Push task_ids to role queues so workers pick them up.
    3. Poll Redis every POLL_INTERVAL seconds until all jobs reach a terminal state
       (review | approved | done | failed).
    4. Advance to next wave.

The executor stores progress to Redis under forgechain:prd:{prd_id} so the
GET /forgechain/prd/{id} endpoint can report live status.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import replace
from typing import Any

import redis.asyncio as aioredis

import sys
for _p in ["/packages", "../../packages"]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from orchestrator import StateMachine, TaskState
from orchestrator.router import TaskRouter
from .models import TaskGraph, PRDTask

logger = logging.getLogger(__name__)

_POLL_INTERVAL = 10        # seconds between state polls
_WAVE_TIMEOUT  = 1800      # 30 minutes max per wave
_TERMINAL_STATES = {"review", "approved", "done", "failed"}

_task_router = TaskRouter()


class WaveExecutor:
    """Execute a TaskGraph wave by wave, writing progress to Redis."""

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url

    async def execute(self, graph: TaskGraph) -> None:
        """Entry point — runs all waves sequentially, waves run jobs in parallel.

        This is designed to be called as an asyncio background task (fire and
        forget from the route handler).
        """
        redis = aioredis.from_url(self._redis_url, decode_responses=True)
        sm = StateMachine(self._redis_url)

        try:
            await self._set_prd_state(redis, graph.prd_id, "executing", current_wave=0)

            # Track prd_task_id → forgechain_task_id mapping
            task_job_map: dict[str, str] = {}
            critical_set: set[str] = set(graph.critical_path)

            for wave in graph.waves:
                logger.info(
                    "[PRD:%s] Starting wave %d (%d tasks)",
                    graph.prd_id, wave.wave_number, len(wave.task_ids),
                )
                await self._set_prd_state(
                    redis, graph.prd_id, "executing",
                    current_wave=wave.wave_number,
                )

                # Create ForgeChain jobs for every task in this wave.
                # Critical path tasks are pushed to the FRONT of their queue
                # (LPUSH) so workers pick them up before non-critical work.
                wave_job_ids: list[str] = []
                critical_ids = [t for t in wave.task_ids if t in critical_set]
                non_critical_ids = [t for t in wave.task_ids if t not in critical_set]

                for prd_task_id in critical_ids + non_critical_ids:
                    task = graph.task_by_id(prd_task_id)
                    if task is None:
                        continue
                    is_critical = prd_task_id in critical_set
                    job_id = await self._enqueue_job(
                        redis, sm, graph.prd_id, task, priority=is_critical
                    )
                    task_job_map[prd_task_id] = job_id
                    wave_job_ids.append(job_id)

                # Persist updated map so GET endpoint can report per-task job links
                await redis.hset(
                    f"forgechain:prd:{graph.prd_id}",
                    "task_job_map",
                    json.dumps(task_job_map),
                )

                logger.info(
                    "[PRD:%s] Wave %d critical path tasks: %s",
                    graph.prd_id, wave.wave_number,
                    [t for t in wave.task_ids if t in critical_set],
                )

                # Poll until all jobs in this wave reach a terminal state
                await self._wait_for_wave(redis, wave_job_ids, graph.prd_id, wave.wave_number)

            await self._set_prd_state(redis, graph.prd_id, "done", current_wave=len(graph.waves))
            logger.info("[PRD:%s] All waves complete", graph.prd_id)

        except Exception as exc:
            logger.exception("[PRD:%s] Execution failed", graph.prd_id)
            await self._set_prd_state(redis, graph.prd_id, "failed", error=str(exc))
        finally:
            await redis.aclose()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _enqueue_job(
        self,
        redis: aioredis.Redis,
        sm: StateMachine,
        prd_id: str,
        task: PRDTask,
        *,
        priority: bool = False,
    ) -> str:
        """Create a ForgeChain job for a single PRD task and push to queue.

        priority=True → LPUSH (front of queue, picked up first by workers).
        priority=False → RPUSH (back of queue, normal FIFO order).
        """
        job_id = str(uuid.uuid4())

        route = _task_router.route(task.description, explicit_role=task.role)

        metadata: dict[str, Any] = {
            "role":        task.role,
            "queue":       route.queue,
            "description": f"[PRD:{prd_id}] {task.title}\n\n{task.description}",
            "prd_id":      prd_id,
            "prd_task_id": task.task_id,
            "pii_policy":  "strict",
            "complexity":  task.complexity,
        }

        # Use complexity to bias starting tier
        if task.complexity == "simple":
            metadata["tier"] = "junior"
        elif task.complexity == "complex":
            metadata["tier"] = "senior"

        await sm.create(job_id, metadata)

        # Critical path → LPUSH (front); non-critical → RPUSH (back)
        if priority:
            await redis.lpush(route.queue, job_id)
        else:
            await redis.rpush(route.queue, job_id)

        logger.info(
            "[PRD:%s] Enqueued job %s for task %s (role=%s queue=%s priority=%s)",
            prd_id, job_id[:8], task.task_id, task.role, route.queue, priority,
        )
        return job_id

    async def _wait_for_wave(
        self,
        redis: aioredis.Redis,
        job_ids: list[str],
        prd_id: str,
        wave_number: int,
    ) -> None:
        """Poll Redis until all jobs in this wave are in a terminal state."""
        deadline = time.monotonic() + _WAVE_TIMEOUT
        pending = set(job_ids)

        while pending and time.monotonic() < deadline:
            still_pending: set[str] = set()
            for job_id in pending:
                state = await redis.hget(f"forgechain:task:{job_id}", "state")
                if state not in _TERMINAL_STATES:
                    still_pending.add(job_id)
                else:
                    logger.info(
                        "[PRD:%s] Wave %d — job %s reached state=%s",
                        prd_id, wave_number, job_id[:8], state,
                    )
            pending = still_pending

            if pending:
                await asyncio.sleep(_POLL_INTERVAL)

        if pending:
            logger.warning(
                "[PRD:%s] Wave %d timed out — %d jobs still pending: %s",
                prd_id, wave_number, len(pending),
                [j[:8] for j in pending],
            )

    async def _set_prd_state(
        self,
        redis: aioredis.Redis,
        prd_id: str,
        state: str,
        current_wave: int = 0,
        error: str = "",
    ) -> None:
        key = f"forgechain:prd:{prd_id}"
        mapping: dict[str, str] = {
            "state":        state,
            "current_wave": str(current_wave),
            "updated_at":   str(time.time()),
        }
        if error:
            mapping["error"] = error
        await redis.hset(key, mapping=mapping)
        await redis.expire(key, 60 * 60 * 24 * 7)  # 7-day TTL
