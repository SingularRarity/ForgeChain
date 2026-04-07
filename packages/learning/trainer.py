"""Trainer — run BootstrapFewShot on accumulated examples, save updated weights.

Called by the nightly Celery beat task (feedback_worker.py).
Workers hot-load new weights on the next task — no restart required.

Algorithm:
  1. Fetch approved examples for (role, tier) from ExampleStore.
  2. Convert to dspy.Example objects with labeled inputs + outputs.
  3. Run BootstrapFewShot — selects demonstrations where the student
     module produces output that passes `patch_metric`.
  4. Save compiled program to $FORGECHAIN_WEIGHTS_DIR/{role}_{tier}.json.
  5. Record training run timestamp in Redis for observability.

Minimum examples required: MIN_EXAMPLES (default 10).
Below that threshold, training is skipped — not enough signal.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import dspy
from dspy.teleprompt import BootstrapFewShot

import sys
for _p in ["/packages", "../../packages"]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from dspy_prompts.signatures import get_signature
from dspy_prompts.lm_config import configure_dspy_lm
from .example_store import ExampleStore

logger = logging.getLogger(__name__)

_WEIGHTS_DIR = Path(os.getenv("FORGECHAIN_WEIGHTS_DIR", "/app/dspy_weights"))
MIN_EXAMPLES = int(os.getenv("FORGECHAIN_MIN_TRAIN_EXAMPLES", "10"))

_ALL_ROLES = ["backend_dev", "frontend_dev", "db_eng", "qa_backend", "ai_eng", "sre", "ba"]
_ALL_TIERS = ["junior", "mid", "senior"]   # cto excluded — never auto-trained


def patch_metric(prediction: dspy.Prediction, example: dspy.Example, trace=None) -> float:
    """Score a prediction against an approved example.

    Metric used by BootstrapFewShot to filter which bootstrapped
    demonstrations are worth keeping as few-shot examples.

    Score: 1.0 if patch is non-trivial AND confidence is high.
           0.5 if patch present but confidence is not high.
           0.0 if patch is empty.
    """
    predicted_patch = getattr(prediction, "patch", "") or ""
    predicted_conf  = getattr(prediction, "confidence", "low").strip().lower()

    if len(predicted_patch.strip()) < 20:
        return 0.0
    if predicted_conf == "high":
        return 1.0
    return 0.5


class Trainer:
    """Nightly trainer — optimises prompts from outcome data."""

    def __init__(self, redis_url: str) -> None:
        self._store = ExampleStore(redis_url)
        self._redis_url = redis_url

    async def run_all(self) -> dict[str, bool]:
        """Train all role/tier combinations that have enough approved examples.

        Returns a mapping of "{role}_{tier}" → True (trained) | False (skipped).
        """
        results: dict[str, bool] = {}
        for role in _ALL_ROLES:
            for tier in _ALL_TIERS:
                key = f"{role}_{tier}"
                try:
                    trained = await self.run_one(role, tier)
                    results[key] = trained
                except Exception:
                    logger.exception("[trainer] Failed for %s", key)
                    results[key] = False
        return results

    async def run_one(self, role: str, tier: str) -> bool:
        """Train a single (role, tier) combination. Returns True if training ran."""
        examples = await self._store.get_examples(role, tier, outcome="approved")

        if len(examples) < MIN_EXAMPLES:
            logger.info(
                "[trainer] Skipping %s/%s — only %d approved examples (need %d)",
                role, tier, len(examples), MIN_EXAMPLES,
            )
            return False

        logger.info(
            "[trainer] Training %s/%s with %d approved examples",
            role, tier, len(examples),
        )

        # Build dspy.Example objects
        dspy_examples = [
            dspy.Example(**ex.to_dspy_inputs(), **ex.to_dspy_outputs()).with_inputs(
                "task_description", "role_context", "retrieved_knowledge"
            )
            for ex in examples
        ]

        # Configure DSPy LM for this tier
        configure_dspy_lm(tier)  # type: ignore[arg-type]

        sig = get_signature(role, tier)  # type: ignore[arg-type]
        student = dspy.ChainOfThought(sig)

        optimizer = BootstrapFewShot(
            metric=patch_metric,
            max_bootstrapped_demos=4,
            max_labeled_demos=4,
        )

        compiled = optimizer.compile(student, trainset=dspy_examples)

        _WEIGHTS_DIR.mkdir(parents=True, exist_ok=True)
        out_path = _WEIGHTS_DIR / f"{role}_{tier}.json"
        compiled.save(str(out_path))

        await self._record_training_run(role, tier, len(examples))
        logger.info("[trainer] Saved updated weights → %s", out_path)
        return True

    async def _record_training_run(self, role: str, tier: str, n_examples: int) -> None:
        import redis.asyncio as aioredis
        redis = aioredis.from_url(self._redis_url, decode_responses=True)
        try:
            key = f"forgechain:training:last_run:{role}_{tier}"
            await redis.hset(key, mapping={
                "ran_at":     str(time.time()),
                "n_examples": str(n_examples),
                "role":       role,
                "tier":       tier,
            })
            await redis.expire(key, 60 * 60 * 24 * 90)  # 90-day TTL
        finally:
            await redis.aclose()
