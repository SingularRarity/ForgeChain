"""Quant Layer — mathematical models that improve from every outcome.

Components:
  bandit.py   — Thompson Sampling for tier routing (Beta distribution per role/tier)
  entropy.py  — Information gain deduplication for KB chunks
  coverage.py — Retrieval score tracking to surface knowledge gaps
  ema.py      — Exponential moving average worker quality + degradation alerts
"""

from __future__ import annotations

from .bandit import BanditRouter
from .entropy import should_ingest, filter_by_entropy
from .coverage import CoverageTracker
from .ema import EMATracker

__all__ = [
    "BanditRouter",
    "should_ingest",
    "filter_by_entropy",
    "CoverageTracker",
    "EMATracker",
]
