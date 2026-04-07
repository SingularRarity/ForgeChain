"""Token consumption + cost ledger.

Every LLM call goes through `TokenLedger.record()`.  Data is written to:
  1. Redis hash  `forgechain:ledger:{task_id}`  — fast per-task lookup
  2. Redis sorted set `forgechain:ledger:all`    — global timeline, score=timestamp
  3. Redis key   `forgechain:ledger:totals`      — running aggregate (HINCRBYFLOAT)

Postgres write is done async (fire-and-forget) when DATABASE_URL is set.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import asdict, dataclass
from typing import Optional

import redis.asyncio as aioredis

from .pricing import CallCost, calculate_cost

logger = logging.getLogger(__name__)

_LEDGER_TTL = 60 * 60 * 24 * 30  # 30 days


@dataclass
class LedgerEntry:
    entry_id: str
    task_id: str
    role: str
    tier: str
    stage: str           # "draft" | "escalation" | "cto_review"
    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    input_cost_usd: float
    output_cost_usd: float
    total_cost_usd: float
    timestamp: float

    @classmethod
    def build(
        cls,
        task_id: str,
        role: str,
        tier: str,
        stage: str,
        cost: CallCost,
    ) -> "LedgerEntry":
        return cls(
            entry_id=str(uuid.uuid4()),
            task_id=task_id,
            role=role,
            tier=tier,
            stage=stage,
            provider=cost.provider,
            model=cost.model,
            prompt_tokens=cost.prompt_tokens,
            completion_tokens=cost.completion_tokens,
            total_tokens=cost.prompt_tokens + cost.completion_tokens,
            input_cost_usd=cost.input_cost_usd,
            output_cost_usd=cost.output_cost_usd,
            total_cost_usd=cost.total_cost_usd,
            timestamp=time.time(),
        )


class TokenLedger:
    """Async ledger — instantiate once per worker, share across calls."""

    def __init__(self, redis_url: str) -> None:
        self._redis = aioredis.from_url(redis_url, decode_responses=True)
        self._db_url: Optional[str] = os.getenv("DATABASE_URL")

    async def record(
        self,
        task_id: str,
        role: str,
        tier: str,
        stage: str,
        provider: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> LedgerEntry:
        cost = calculate_cost(provider, model, prompt_tokens, completion_tokens)
        entry = LedgerEntry.build(task_id, role, tier, stage, cost)

        await self._write_redis(entry)

        if self._db_url:
            try:
                await self._write_postgres(entry)
            except Exception:
                logger.warning("Ledger Postgres write failed (non-fatal)", exc_info=True)

        logger.info(
            "[ledger] task=%s role=%s tier=%s stage=%s provider=%s model=%s "
            "tokens=%d cost=$%.6f",
            task_id, role, tier, stage, provider, model,
            entry.total_tokens, entry.total_cost_usd,
        )
        return entry

    async def get_task_summary(self, task_id: str) -> dict:
        raw = await self._redis.hgetall(f"forgechain:ledger:{task_id}")
        if not raw:
            return {}
        calls = json.loads(raw.get("calls", "[]"))
        return {
            "task_id": task_id,
            "total_tokens": int(raw.get("total_tokens", 0)),
            "total_cost_usd": float(raw.get("total_cost_usd", 0.0)),
            "calls": calls,
        }

    async def get_global_totals(self) -> dict:
        raw = await self._redis.hgetall("forgechain:ledger:totals")
        return {k: float(v) for k, v in raw.items()}

    # ------------------------------------------------------------------ #

    async def _write_redis(self, entry: LedgerEntry) -> None:
        task_key = f"forgechain:ledger:{entry.task_id}"
        call_data = {
            "entry_id": entry.entry_id,
            "tier": entry.tier,
            "stage": entry.stage,
            "provider": entry.provider,
            "model": entry.model,
            "prompt_tokens": entry.prompt_tokens,
            "completion_tokens": entry.completion_tokens,
            "total_cost_usd": round(entry.total_cost_usd, 8),
            "timestamp": entry.timestamp,
        }

        # Append call to per-task list
        existing = await self._redis.hget(task_key, "calls")
        calls: list = json.loads(existing) if existing else []
        calls.append(call_data)

        pipe = self._redis.pipeline()
        pipe.hset(task_key, mapping={
            "calls": json.dumps(calls),
            "total_tokens": sum(c["prompt_tokens"] + c["completion_tokens"] for c in calls),
            "total_cost_usd": round(sum(c["total_cost_usd"] for c in calls), 8),
        })
        pipe.expire(task_key, _LEDGER_TTL)

        # Global timeline (score = timestamp)
        pipe.zadd("forgechain:ledger:all", {json.dumps(call_data): entry.timestamp})

        # Global running totals per provider
        pipe.hincrbyfloat("forgechain:ledger:totals", f"{entry.provider}:tokens", entry.total_tokens)
        pipe.hincrbyfloat("forgechain:ledger:totals", f"{entry.provider}:cost_usd", entry.total_cost_usd)
        pipe.hincrbyfloat("forgechain:ledger:totals", "all:tokens", entry.total_tokens)
        pipe.hincrbyfloat("forgechain:ledger:totals", "all:cost_usd", entry.total_cost_usd)

        await pipe.execute()

    async def _write_postgres(self, entry: LedgerEntry) -> None:
        import asyncpg  # optional dep — only imported when DB_URL set
        conn = await asyncpg.connect(self._db_url)
        try:
            await conn.execute(
                """
                INSERT INTO fc_token_ledger (
                    entry_id, task_id, role, tier, stage,
                    provider, model,
                    prompt_tokens, completion_tokens, total_tokens,
                    input_cost_usd, output_cost_usd, total_cost_usd,
                    recorded_at
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,
                          to_timestamp($14))
                """,
                entry.entry_id, entry.task_id, entry.role, entry.tier, entry.stage,
                entry.provider, entry.model,
                entry.prompt_tokens, entry.completion_tokens, entry.total_tokens,
                entry.input_cost_usd, entry.output_cost_usd, entry.total_cost_usd,
                entry.timestamp,
            )
        finally:
            await conn.close()

    async def close(self) -> None:
        await self._redis.aclose()
