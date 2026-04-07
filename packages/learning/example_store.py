"""ExampleStore — dual-write training examples to Redis (fast) and Postgres (durable).

Redis list ``forgechain:examples:{role}_{tier}`` holds the last N JSON-encoded
examples for fast trainer reads (no DB query during nightly training).

Postgres ``fc_examples`` is the append-only source of truth.

The split is intentional:
  - Redis  → trainer reads O(1), no DB pressure during nightly jobs
  - Postgres → survives Redis eviction, supports analytics queries later
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, asdict
from typing import Literal

import asyncpg
import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

Outcome = Literal["approved", "rejected"]

# Keep last 500 examples per (role, tier) in Redis for fast batch reads
_REDIS_MAX_EXAMPLES = 500


@dataclass(frozen=True)
class Example:
    """A single training example extracted from a completed ForgeChain task."""

    task_id: str
    role: str
    tier: str
    task_description: str
    role_context: str
    retrieved_knowledge: str
    patch: str
    reasoning: str
    confidence: str       # "high" (approved) | "low" (rejected)
    outcome: Outcome
    rejection_reason: str
    recorded_at: float = 0.0

    def to_dspy_inputs(self) -> dict:
        """Return the DSPy input fields for this example."""
        return {
            "task_description":    self.task_description,
            "role_context":        self.role_context,
            "retrieved_knowledge": self.retrieved_knowledge,
        }

    def to_dspy_outputs(self) -> dict:
        """Return the expected DSPy output fields for this example."""
        out: dict = {"patch": self.patch, "confidence": self.confidence}
        if self.reasoning:
            out["reasoning"] = self.reasoning
        return out


class ExampleStore:
    """Persist and retrieve training examples."""

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url
        self._pg_dsn = os.getenv("DATABASE_URL")

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def save(self, example: Example) -> None:
        """Persist example to Redis list and Postgres (non-blocking on PG failure)."""
        ex = Example(
            task_id=example.task_id,
            role=example.role,
            tier=example.tier,
            task_description=example.task_description,
            role_context=example.role_context,
            retrieved_knowledge=example.retrieved_knowledge,
            patch=example.patch,
            reasoning=example.reasoning,
            confidence=example.confidence,
            outcome=example.outcome,
            rejection_reason=example.rejection_reason,
            recorded_at=time.time(),
        )
        await self._write_redis(ex)
        await self._write_postgres(ex)

    async def _write_redis(self, ex: Example) -> None:
        key = _redis_key(ex.role, ex.tier)
        redis = aioredis.from_url(self._redis_url, decode_responses=True)
        try:
            payload = json.dumps(asdict(ex))
            await redis.rpush(key, payload)
            # Trim to last N to keep memory bounded
            await redis.ltrim(key, -_REDIS_MAX_EXAMPLES, -1)
            await redis.expire(key, 60 * 60 * 24 * 30)  # 30-day TTL
        finally:
            await redis.aclose()

    async def _write_postgres(self, ex: Example) -> None:
        if not self._pg_dsn:
            return
        try:
            conn = await asyncpg.connect(self._pg_dsn)
            try:
                await conn.execute(
                    """
                    INSERT INTO fc_examples
                        (entry_id, task_id, role, tier, task_description,
                         role_context, patch, reasoning, confidence,
                         outcome, rejection_reason, recorded_at)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,to_timestamp($12))
                    ON CONFLICT (entry_id) DO NOTHING
                    """,
                    str(uuid.uuid4()),
                    ex.task_id,
                    ex.role,
                    ex.tier,
                    ex.task_description[:2000],
                    ex.role_context,
                    ex.patch[:8000],
                    ex.reasoning[:2000],
                    ex.confidence,
                    ex.outcome,
                    ex.rejection_reason[:500],
                    ex.recorded_at,
                )
            finally:
                await conn.close()
        except Exception:
            logger.warning("[example_store] Postgres write failed", exc_info=True)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def get_examples(
        self,
        role: str,
        tier: str,
        *,
        outcome: Outcome | None = None,
        limit: int = 200,
    ) -> list[Example]:
        """Fetch recent examples from Redis for trainer consumption."""
        key = _redis_key(role, tier)
        redis = aioredis.from_url(self._redis_url, decode_responses=True)
        try:
            raw_list = await redis.lrange(key, 0, -1)
        finally:
            await redis.aclose()

        examples: list[Example] = []
        for raw in raw_list:
            try:
                data = json.loads(raw)
                ex = Example(**{k: data.get(k, "") for k in Example.__dataclass_fields__})
                if outcome is None or ex.outcome == outcome:
                    examples.append(ex)
            except Exception:
                logger.debug("[example_store] Skipping malformed example entry")

        # Return most recent first, capped at limit
        return examples[-limit:]

    async def count(self, role: str, tier: str, outcome: Outcome | None = None) -> int:
        examples = await self.get_examples(role, tier, outcome=outcome, limit=_REDIS_MAX_EXAMPLES)
        return len(examples)


def _redis_key(role: str, tier: str) -> str:
    return f"forgechain:examples:{role}_{tier}"
