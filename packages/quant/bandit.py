"""Thompson Sampling bandit for tier routing.

Replaces the static ROLE_BASE_TIER map with a multi-armed bandit that learns
which tier actually produces approved patches for each role.

Model: Beta distribution per (role, tier) pair.
  α = count of approved patches from this (role, tier) pair
  β = count of rejected patches from this (role, tier) pair

Routing decision:
  For each eligible tier: sample s ~ Beta(α + 1, β + 1)
  Route to the tier with the highest sampled s.

Effect:
  - Cheaper tiers (Ollama/Grok) get priority when they succeed.
  - Automatically escalates to Gemini/Claude when cheaper tiers fail.
  - Converges to the optimal tier per role without manual tuning.
  - Add FORGECHAIN_USE_BANDIT=1 to env to activate; defaults to static routing.

Redis keys:
  forgechain:bandit:{role}:{tier}  →  hash { alpha, beta }
"""

from __future__ import annotations

import logging
import os
import random
from typing import Literal

import redis as sync_redis

logger = logging.getLogger(__name__)

Tier = Literal["junior", "mid", "senior"]

# Tiers eligible for bandit routing (cto is manual-only — never included)
_TIER_ORDER: list[Tier] = ["junior", "mid", "senior"]

# Role max-auto ceiling (mirrors tiered_router — bandit never exceeds this)
_ROLE_CEILING: dict[str, Tier] = {
    "ba":           "mid",
    "qa_backend":   "senior",
    "frontend_dev": "senior",
    "backend_dev":  "senior",
    "sre":          "senior",
    "db_eng":       "senior",
    "ai_eng":       "senior",
}

# Default starting tier — used to initialise α (warm start avoids cold-start
# over-exploration on cheap tiers before any data exists)
_ROLE_WARM_START: dict[str, Tier] = {
    "ba":           "junior",
    "qa_backend":   "junior",
    "frontend_dev": "mid",
    "backend_dev":  "mid",
    "sre":          "mid",
    "db_eng":       "senior",
    "ai_eng":       "senior",
}

_ENABLED = os.getenv("FORGECHAIN_USE_BANDIT", "0") == "1"


class BanditRouter:
    """Thompson Sampling multi-armed bandit for tier selection."""

    def __init__(self, redis_url: str) -> None:
        self._redis = sync_redis.from_url(redis_url, decode_responses=True)

    def get_tier(self, role: str) -> Tier:
        """Sample Beta distributions and return the highest-scoring eligible tier.

        Falls back to static routing if bandit is disabled or Redis is unavailable.
        """
        if not _ENABLED:
            from providers.tiered_router import ROLE_BASE_TIER
            return ROLE_BASE_TIER.get(role, "mid")  # type: ignore[return-value]

        ceiling = _TIER_ORDER.index(_ROLE_CEILING.get(role, "senior"))
        eligible = _TIER_ORDER[: ceiling + 1]

        best_tier: Tier = eligible[0]
        best_score: float = -1.0

        for tier in eligible:
            alpha, beta = self._get_counts(role, tier)
            # Beta(α+1, β+1) — add 1 to avoid Beta(0,0) which is undefined
            score = random.betavariate(alpha + 1.0, beta + 1.0)
            logger.debug(
                "[bandit] %s/%s α=%d β=%d → sample=%.3f",
                role, tier, alpha, beta, score,
            )
            if score > best_score:
                best_score = score
                best_tier = tier

        logger.info("[bandit] %s → %s (score=%.3f)", role, best_tier, best_score)
        return best_tier

    def record_outcome(self, role: str, tier: str, *, approved: bool) -> None:
        """Increment α (approved) or β (rejected) for this (role, tier) pair."""
        key = _bandit_key(role, tier)
        field = "alpha" if approved else "beta"
        try:
            self._redis.hincrby(key, field, 1)
            self._redis.expire(key, 60 * 60 * 24 * 365)  # 1-year TTL
            logger.debug(
                "[bandit] recorded %s for %s/%s",
                "approval" if approved else "rejection", role, tier,
            )
        except Exception:
            logger.warning("[bandit] Redis write failed", exc_info=True)

    def get_stats(self) -> dict[str, dict[str, dict[str, int]]]:
        """Return current α/β counts for all tracked (role, tier) pairs.

        Returns: { role: { tier: { alpha, beta, total, approval_rate } } }
        """
        stats: dict[str, dict] = {}
        try:
            pattern = "forgechain:bandit:*:*"
            for key in self._redis.scan_iter(pattern):
                parts = key.split(":")
                if len(parts) != 4:
                    continue
                _, _, role, tier = parts
                data = self._redis.hgetall(key)
                alpha = int(data.get("alpha", 0))
                beta  = int(data.get("beta", 0))
                total = alpha + beta
                stats.setdefault(role, {})[tier] = {
                    "alpha":         alpha,
                    "beta":          beta,
                    "total":         total,
                    "approval_rate": round(alpha / total, 3) if total > 0 else None,
                }
        except Exception:
            logger.warning("[bandit] get_stats failed", exc_info=True)
        return stats

    def _get_counts(self, role: str, tier: str) -> tuple[float, float]:
        """Return (alpha, beta) for (role, tier), initialising with warm-start if absent."""
        key = _bandit_key(role, tier)
        try:
            data = self._redis.hgetall(key)
            alpha = float(data.get("alpha", 0))
            beta  = float(data.get("beta", 0))

            # Warm start: give default tier a head start (2 pseudo-approvals)
            # so the bandit doesn't thrash to cheap tiers on the very first tasks
            if alpha == 0 and beta == 0:
                warm_tier = _ROLE_WARM_START.get(role, "mid")
                if tier == warm_tier:
                    alpha = 2.0

            return alpha, beta
        except Exception:
            return 0.0, 0.0


def _bandit_key(role: str, tier: str) -> str:
    return f"forgechain:bandit:{role}:{tier}"
