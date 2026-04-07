"""ForgeChain task state machine.

States:
  pending → running → review → approved → done
                    ↘ rejected → pending  (retry)
                    ↘ failed              (terminal)

Transitions are stored as task metadata in Redis under key
``forgechain:task:{task_id}``.
"""

from __future__ import annotations

import json
import time
from enum import Enum
from typing import Any

import redis as sync_redis
import redis.asyncio as aioredis


class TaskState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    REVIEW = "review"      # waiting for human approval
    APPROVED = "approved"  # human approved PR
    REJECTED = "rejected"  # human rejected → re-queue
    DONE = "done"
    FAILED = "failed"


_ALLOWED_TRANSITIONS: dict[TaskState, set[TaskState]] = {
    TaskState.PENDING: {TaskState.RUNNING},
    TaskState.RUNNING: {TaskState.REVIEW, TaskState.FAILED},
    TaskState.REVIEW: {TaskState.APPROVED, TaskState.REJECTED},
    TaskState.APPROVED: {TaskState.DONE},
    TaskState.REJECTED: {TaskState.PENDING},
    TaskState.DONE: set(),
    TaskState.FAILED: {TaskState.PENDING},  # allow manual retry
}

_KEY_PREFIX = "forgechain:task:"
_TTL_SECONDS = 60 * 60 * 24 * 7  # 7 days


class StateMachine:
    """Async state machine backed by Redis hashes."""

    def __init__(self, redis_url: str) -> None:
        self._redis = aioredis.from_url(redis_url, decode_responses=True)

    def _key(self, task_id: str) -> str:
        return f"{_KEY_PREFIX}{task_id}"

    async def create(self, task_id: str, metadata: dict[str, Any]) -> None:
        key = self._key(task_id)
        payload = {
            "task_id": task_id,
            "state": TaskState.PENDING,
            "created_at": time.time(),
            "updated_at": time.time(),
            **{k: json.dumps(v) if isinstance(v, (dict, list)) else str(v) for k, v in metadata.items()},
        }
        await self._redis.hset(key, mapping=payload)
        await self._redis.expire(key, _TTL_SECONDS)

    async def transition(self, task_id: str, new_state: TaskState, extra: dict[str, Any] | None = None) -> None:
        key = self._key(task_id)
        current_raw = await self._redis.hget(key, "state")
        if current_raw is None:
            raise KeyError(f"Task {task_id!r} not found in state machine")
        current = TaskState(current_raw)
        if new_state not in _ALLOWED_TRANSITIONS[current]:
            raise ValueError(
                f"Illegal transition {current} → {new_state} for task {task_id}"
            )
        updates: dict[str, str] = {
            "state": new_state.value,
            "updated_at": str(time.time()),
        }
        if extra:
            updates.update(
                {k: json.dumps(v) if isinstance(v, (dict, list)) else str(v) for k, v in extra.items()}
            )
        await self._redis.hset(key, mapping=updates)

    async def get(self, task_id: str) -> dict[str, Any] | None:
        data = await self._redis.hgetall(self._key(task_id))
        return data or None

    async def close(self) -> None:
        await self._redis.aclose()
