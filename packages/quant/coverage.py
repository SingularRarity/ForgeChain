"""Coverage scoring — detect knowledge gaps by tracking retrieval scores.

For every task, the Retriever returns a set of chunks with cosine similarity
scores. A role with consistently low scores has a knowledge gap — the agent
is working without relevant documentation.

This module tracks the rolling average retrieval score per role and surfaces
gaps via GET /forgechain/knowledge/gaps.

Redis keys:
  forgechain:coverage:{role}  →  hash {
      total_queries:  int
      score_sum:      float
      avg_score:      float   (score_sum / total_queries)
      last_queries:   JSON list of last 5 query strings (for recommendations)
  }
"""

from __future__ import annotations

import json
import logging
import math
import os
from typing import Any

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

_GAP_THRESHOLD = float(os.getenv("FORGECHAIN_COVERAGE_GAP_THRESHOLD", "0.25"))
_ALL_ROLES = ["backend_dev", "frontend_dev", "db_eng", "qa_backend", "ai_eng", "sre", "ba"]

# Recommendation hints per role — surfaced when gap is detected
_ROLE_DOCS: dict[str, str] = {
    "backend_dev":  "Ingest: FastAPI docs, SQLAlchemy docs, project skills/backend_dev.md",
    "frontend_dev": "Ingest: React 18 docs, Tailwind CSS docs, project skills/frontend_dev.md",
    "db_eng":       "Ingest: PostgreSQL 16 docs, Alembic migration guide, skills/db_eng.md",
    "qa_backend":   "Ingest: pytest-asyncio docs, httpx docs, skills/qa_backend.md",
    "ai_eng":       "Ingest: HuggingFace transformers docs, PyTorch docs",
    "sre":          "Ingest: Docker Compose docs, GitHub Actions docs, skills/sre.md",
    "ba":           "Ingest: project ADRs, product specs, skills/ba.md",
}


class CoverageTracker:
    """Track retrieval scores per role and surface coverage gaps."""

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url

    async def record(
        self,
        role: str,
        query: str,
        scores: list[float],
    ) -> None:
        """Update rolling stats for a role after a retrieval call.

        scores: list of cosine similarity values from the retriever hits.
                Empty list means zero hits — counted as score 0.0.
        """
        avg = sum(scores) / len(scores) if scores else 0.0
        redis = aioredis.from_url(self._redis_url, decode_responses=True)
        key = _coverage_key(role)
        try:
            pipe = redis.pipeline()
            pipe.hincrbyfloat(key, "score_sum", avg)
            pipe.hincrby(key, "total_queries", 1)
            await pipe.execute()

            # Recalculate avg_score
            data = await redis.hgetall(key)
            total = int(data.get("total_queries", 1))
            score_sum = float(data.get("score_sum", avg))
            new_avg = score_sum / total if total > 0 else 0.0
            await redis.hset(key, "avg_score", str(round(new_avg, 4)))

            # Keep last 5 query strings for surfacing in gap recommendations
            last_raw = data.get("last_queries", "[]")
            try:
                last: list[str] = json.loads(last_raw)
            except Exception:
                last = []
            last.append(query[:120])
            await redis.hset(key, "last_queries", json.dumps(last[-5:]))
            await redis.expire(key, 60 * 60 * 24 * 90)  # 90-day TTL
        finally:
            await redis.aclose()

    async def get_gaps(
        self,
        *,
        threshold: float = _GAP_THRESHOLD,
        min_queries: int = 5,
    ) -> list[dict[str, Any]]:
        """Return roles whose avg retrieval score is below threshold.

        min_queries: ignore roles with fewer than this many queries
                     (not enough signal to call it a gap).
        """
        redis = aioredis.from_url(self._redis_url, decode_responses=True)
        gaps: list[dict[str, Any]] = []
        try:
            for role in _ALL_ROLES:
                key = _coverage_key(role)
                data = await redis.hgetall(key)
                if not data:
                    continue

                total = int(data.get("total_queries", 0))
                if total < min_queries:
                    continue

                avg_score = float(data.get("avg_score", 1.0))
                if avg_score >= threshold:
                    continue

                sample_queries: list[str] = []
                try:
                    sample_queries = json.loads(data.get("last_queries", "[]"))
                except Exception:
                    pass

                gaps.append({
                    "role":                role,
                    "avg_retrieval_score": round(avg_score, 3),
                    "total_queries":       total,
                    "sample_queries":      sample_queries,
                    "recommendation":      _ROLE_DOCS.get(role, f"Ingest documentation for {role}"),
                })

            # Sort by worst coverage first
            gaps.sort(key=lambda g: g["avg_retrieval_score"])
        finally:
            await redis.aclose()

        return gaps

    async def get_all_scores(self) -> dict[str, float]:
        """Return avg retrieval score per role for observability."""
        redis = aioredis.from_url(self._redis_url, decode_responses=True)
        scores: dict[str, float] = {}
        try:
            for role in _ALL_ROLES:
                data = await redis.hgetall(_coverage_key(role))
                if data:
                    scores[role] = float(data.get("avg_score", 0.0))
        finally:
            await redis.aclose()
        return scores


def _coverage_key(role: str) -> str:
    return f"forgechain:coverage:{role}"
