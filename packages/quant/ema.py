"""Exponential Moving Average quality tracker per worker role.

Tracks per-role approval rate with EMA (α = 0.1 — slow decay, sensitive
to trend) and flags sustained quality degradation.

  quality[role] = α × outcome + (1 - α) × quality[role]
    where outcome = 1.0 (approved) or 0.0 (rejected)

  When quality[role] < 0.6 for 7 consecutive days:
    → flag to operator via GET /forgechain/health/workers

Redis keys:
  forgechain:ema:{role}  →  hash {
      quality:          float   current EMA value
      below_since:      float   epoch when quality first dropped below 0.6 (0 if above)
      total_approved:   int
      total_rejected:   int
      last_updated:     float   epoch
  }
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

import redis.asyncio as aioredis

import sys
for _p in ["/packages", "../../packages"]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from notify import dispatcher as _notifier

logger = logging.getLogger(__name__)

EMA_ALPHA   = float(os.getenv("FORGECHAIN_EMA_ALPHA", "0.1"))
QUALITY_THRESHOLD = 0.6
DEGRADATION_DAYS  = 7
_INITIAL_QUALITY  = 0.7   # warm start — slightly above threshold

_ALL_ROLES = ["backend_dev", "frontend_dev", "db_eng", "qa_backend", "ai_eng", "sre", "ba"]


class EMATracker:
    """EMA quality tracker — updated on every approve/reject event."""

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url

    async def update(self, role: str, *, approved: bool) -> float:
        """Apply EMA update and return the new quality score."""
        redis = aioredis.from_url(self._redis_url, decode_responses=True)
        key = _ema_key(role)
        now = time.time()
        outcome = 1.0 if approved else 0.0

        try:
            data = await redis.hgetall(key)
            current_quality = float(data.get("quality", _INITIAL_QUALITY))
            new_quality = EMA_ALPHA * outcome + (1.0 - EMA_ALPHA) * current_quality

            # Track sustained degradation
            below_since = float(data.get("below_since", 0))
            just_crossed_below = False
            if new_quality < QUALITY_THRESHOLD:
                if below_since == 0:
                    below_since = now   # first day below threshold
                    just_crossed_below = True
            else:
                below_since = 0         # reset — quality recovered

            # Counters
            approved_count  = int(data.get("total_approved",  0)) + (1 if approved else 0)
            rejected_count  = int(data.get("total_rejected",  0)) + (0 if approved else 1)

            await redis.hset(key, mapping={
                "quality":        str(round(new_quality, 4)),
                "below_since":    str(below_since),
                "total_approved": str(approved_count),
                "total_rejected": str(rejected_count),
                "last_updated":   str(now),
            })
            await redis.expire(key, 60 * 60 * 24 * 365)   # 1-year TTL

            logger.debug(
                "[ema] %s quality %.3f → %.3f (%s)",
                role, current_quality, new_quality,
                "approved" if approved else "rejected",
            )

            if just_crossed_below:
                try:
                    loop = asyncio.get_event_loop()
                    loop.create_task(_notifier.notify(
                        "ema_degraded",
                        role=role,
                        quality=new_quality,
                        days_below=0,
                    ))
                except RuntimeError:
                    # No running event loop (e.g. sync Celery context) — skip notification
                    pass

            return new_quality

        finally:
            await redis.aclose()

    async def get_quality(self, role: str) -> float:
        """Return current EMA quality for a role (default _INITIAL_QUALITY if unseen)."""
        redis = aioredis.from_url(self._redis_url, decode_responses=True)
        try:
            data = await redis.hgetall(_ema_key(role))
            return float(data.get("quality", _INITIAL_QUALITY)) if data else _INITIAL_QUALITY
        finally:
            await redis.aclose()

    async def get_all_health(self) -> dict[str, dict[str, Any]]:
        """Return health summary for all roles.

        Each entry includes quality, degradation flag, and counts.
        """
        redis = aioredis.from_url(self._redis_url, decode_responses=True)
        now = time.time()
        health: dict[str, dict[str, Any]] = {}

        try:
            for role in _ALL_ROLES:
                data = await redis.hgetall(_ema_key(role))
                if not data:
                    health[role] = {
                        "quality":           _INITIAL_QUALITY,
                        "degraded":          False,
                        "days_below_threshold": 0,
                        "total_approved":    0,
                        "total_rejected":    0,
                        "recommendation":    None,
                    }
                    continue

                quality     = float(data.get("quality", _INITIAL_QUALITY))
                below_since = float(data.get("below_since", 0))
                days_below  = 0
                degraded    = False

                if below_since > 0:
                    days_below = int((now - below_since) / 86400)
                    degraded   = days_below >= DEGRADATION_DAYS

                health[role] = {
                    "quality":              round(quality, 3),
                    "degraded":             degraded,
                    "days_below_threshold": days_below,
                    "total_approved":       int(data.get("total_approved", 0)),
                    "total_rejected":       int(data.get("total_rejected", 0)),
                    "recommendation":       (
                        f"Quality degraded for {days_below}d. "
                        "Run knowledge gap analysis: GET /forgechain/knowledge/gaps"
                    ) if degraded else None,
                }
        finally:
            await redis.aclose()

        return health

    async def get_degraded_roles(self) -> list[str]:
        """Return roles with quality < threshold for >= DEGRADATION_DAYS."""
        health = await self.get_all_health()
        return [role for role, h in health.items() if h["degraded"]]


def _ema_key(role: str) -> str:
    return f"forgechain:ema:{role}"
