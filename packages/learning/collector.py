"""Collector — extracts training examples from completed ForgeChain tasks.

Called immediately after a human approves or rejects a PR.
Reads task metadata from Redis, constructs an Example, persists it via
ExampleStore, and (for approvals) triggers auto-ingestion into ChromaDB.

Usage (from forgechain_router.py):
    collector = Collector(redis_url)
    await collector.on_approved(task_id)
    await collector.on_rejected(task_id, rejection_reason)
"""

from __future__ import annotations

import logging
from typing import Any

import redis.asyncio as aioredis

from .example_store import ExampleStore, Example
from .auto_ingest import AutoIngestor

import sys
for _p in ["/packages", "../../packages"]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from quant.bandit import BanditRouter
from quant.ema import EMATracker

logger = logging.getLogger(__name__)


class Collector:
    """Extract and persist training examples from task state in Redis."""

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url
        self._store = ExampleStore(redis_url)
        self._ingestor = AutoIngestor()
        self._bandit = BanditRouter(redis_url)
        self._ema = EMATracker(redis_url)

    async def on_approved(self, task_id: str) -> None:
        """Build a positive example from an approved task, persist + ingest into KB."""
        task = await self._fetch_task(task_id)
        if task is None:
            return

        role = task.get("role", "backend_dev")
        tier = task.get("tier_used") or task.get("tier") or "mid"
        description = task.get("description", "")
        patch = task.get("patch", "")

        if not patch:
            logger.debug("[collector] task %s has no patch — skipping", task_id[:8])
            return

        example = Example(
            task_id=task_id,
            role=role,
            tier=tier,
            task_description=description,
            role_context=_role_context(role),
            retrieved_knowledge="",   # re-retrieved at inference time
            patch=patch,
            reasoning=task.get("arch_notes", "") or task.get("self_review", ""),
            confidence="high",        # approved = high confidence label
            outcome="approved",
            rejection_reason="",
        )

        await self._store.save(example)
        logger.info(
            "[collector] Saved POSITIVE example task=%s role=%s tier=%s",
            task_id[:8], role, tier,
        )

        # Quant: record approval to bandit (α++) and EMA
        try:
            self._bandit.record_outcome(role, tier, approved=True)
            await self._ema.update(role, approved=True)
        except Exception:
            logger.debug("[collector] Quant update failed on approval", exc_info=True)

        # Auto-ingest approved patch back into the role's ChromaDB KB
        if patch:
            project_id = task.get("project") or None
            try:
                await self._ingestor.ingest(
                    task_id=task_id,
                    role=role,
                    description=description,
                    patch=patch,
                    project_id=project_id,
                )
            except Exception:
                logger.warning(
                    "[collector] Auto-ingest failed for task %s", task_id[:8],
                    exc_info=True,
                )

    async def on_rejected(self, task_id: str, rejection_reason: str) -> None:
        """Build a negative example from a rejected task and persist it."""
        task = await self._fetch_task(task_id)
        if task is None:
            return

        role = task.get("role", "backend_dev")
        tier = task.get("tier_used") or task.get("tier") or "mid"

        example = Example(
            task_id=task_id,
            role=role,
            tier=tier,
            task_description=task.get("description", ""),
            role_context=_role_context(role),
            retrieved_knowledge="",
            patch=task.get("patch", ""),
            reasoning="",
            confidence="low",         # rejected = low confidence label
            outcome="rejected",
            rejection_reason=rejection_reason,
        )

        await self._store.save(example)
        logger.info(
            "[collector] Saved NEGATIVE example task=%s role=%s tier=%s reason=%r",
            task_id[:8], role, tier, rejection_reason[:80],
        )

        # Quant: record rejection to bandit (β++) and EMA
        try:
            self._bandit.record_outcome(role, tier, approved=False)
            await self._ema.update(role, approved=False)
        except Exception:
            logger.debug("[collector] Quant update failed on rejection", exc_info=True)

    # ------------------------------------------------------------------

    async def _fetch_task(self, task_id: str) -> dict[str, Any] | None:
        redis = aioredis.from_url(self._redis_url, decode_responses=True)
        try:
            data = await redis.hgetall(f"forgechain:task:{task_id}")
            return data or None
        finally:
            await redis.aclose()


def _role_context(role: str) -> str:
    _MAP = {
        "backend_dev":   "Python/FastAPI backend engineer",
        "frontend_dev":  "React/TypeScript frontend engineer",
        "db_eng":        "PostgreSQL database engineer",
        "qa_backend":    "Backend QA / pytest specialist",
        "ai_eng":        "ML/AI engineer (PyTorch, HuggingFace)",
        "sre":           "Site reliability / DevOps engineer",
        "ba":            "Business analyst / requirements engineer",
        "solidity_dev":  "Solidity/EVM smart contract engineer — safe, gas-optimized, Foundry-tested",
    }
    return _MAP.get(role, f"Software engineer — {role}")
