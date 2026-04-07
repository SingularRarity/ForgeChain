"""Feedback Worker — nightly Celery beat task that trains DSPy from outcomes.

Beat schedule: runs at 02:00 UTC every day.

What it does:
  1. For every (role, tier) combination with ≥ MIN_EXAMPLES approved examples,
     run BootstrapFewShot and save updated weights to disk.
  2. Workers hot-load new weights on their next task — no restart needed.
  3. Emit a summary to the log so operators can see training progress.

To register the beat schedule, add to the Celery app config:
    app.conf.beat_schedule = {
        "nightly-feedback-training": {
            "task": "forgechain_feedback_train",
            "schedule": crontab(hour=2, minute=0),
        }
    }

Or run standalone (dev/testing):
    python -m apps.workers.feedback_worker
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

sys.path.insert(0, "/app")
for _p in ["/packages", "../../packages"]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from celery import Celery
from celery.schedules import crontab

from learning.trainer import Trainer

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

_REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")

app = Celery(
    "forgechain-feedback",
    broker=os.environ.get("CELERY_BROKER_URL", _REDIS_URL),
    backend=None,
)
app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    task_ignore_result=True,
    timezone="UTC",
    enable_utc=True,
    beat_schedule={
        "nightly-feedback-training": {
            "task": "forgechain_feedback_train",
            "schedule": crontab(hour=2, minute=0),
            "options": {"expires": 3600},   # drop if not consumed within 1 hour
        }
    },
)


@app.task(name="forgechain_feedback_train")
def feedback_train() -> None:
    """Run DSPy training for all roles and tiers with sufficient examples."""
    logger.info("[feedback_worker] Starting nightly training run")
    asyncio.run(_run_training())


async def _run_training() -> None:
    trainer = Trainer(_REDIS_URL)
    results = await trainer.run_all()

    trained  = [k for k, v in results.items() if v]
    skipped  = [k for k, v in results.items() if not v]

    logger.info(
        "[feedback_worker] Training complete. Trained: %d  Skipped: %d",
        len(trained), len(skipped),
    )
    if trained:
        logger.info("[feedback_worker] Updated weights: %s", ", ".join(trained))
    if skipped:
        logger.info("[feedback_worker] Skipped (insufficient examples): %s", ", ".join(skipped))


if __name__ == "__main__":
    # Allow running the training job directly without Celery:
    #   python feedback_worker.py
    asyncio.run(_run_training())
